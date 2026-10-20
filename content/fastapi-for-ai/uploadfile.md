---
id: uploadfile
module: fastapi-for-ai
name: UploadFile
kind: tool
difficulty: intro
est_minutes: 12
status: fact-checked
related:
  - fastapi-for-ai/request-size-limits-and-upload-security
  - fastapi-for-ai/backgroundtasks
  - fastapi-for-ai/job-queues-and-durable-workers
  - fastapi-for-ai/async-endpoints
  - fastapi-for-ai/pydantic-request-response-models
  - multimodal/speech-to-text
  - rag-retrieval/document-parsing
sources:
  - Official docs: FastAPI (Request Files)
  - Official docs: Starlette (Requests, UploadFile)
---

## TL;DR
`UploadFile` is FastAPI's parameter type for files sent as `multipart/form-data`: PDFs for RAG, images
for vision models, audio for transcription. It wraps a spooled temporary file, held in memory while
small and moved to disk when large, so a file doesn't have to fit in RAM. It also exposes filename,
content type and async read/seek methods. Prefer it to `bytes = File()`, but it does no size or
safety checks for you.

## Why it exists
Browsers and HTTP clients send files as `multipart/form-data`: a body split into parts by a boundary
string, each part carrying its own headers (`Content-Disposition` with field name and filename,
`Content-Type`) and raw bytes. JSON can't carry binary data efficiently, and base64 inflates it by a
third. So an AI service that ingests documents, screenshots or voice notes must parse multipart.

The naive option is to declare the parameter as `bytes = File()`. FastAPI then reads the whole part
into one `bytes` object. For a 2 KB avatar that is fine. For a 300 MB audio file it means 300 MB per
concurrent request held in worker memory, and that memory isn't released until the handler ends. Ten
simultaneous uploads can OOM-kill a pod. Reading `request.stream()` yourself avoids the memory cost but
means writing your own multipart parser. `UploadFile` sits in between: Starlette's multipart parser
streams each file part into a `SpooledTemporaryFile`, which stays in memory below a threshold (1 MiB
in current Starlette) and rolls over to a temporary file on disk above it. You get a file-like object with metadata, bounded memory,
and no parser to write.

## How it works
### Declaring it
`async def ingest(file: UploadFile)` declares one required file field named `file`. Use
`files: list[UploadFile]` for several, `UploadFile | None = None` for optional, and add regular form
fields with `Form()`: `title: str = Form()`. Multipart support requires the `python-multipart` package.
FastAPI raises an error at startup that names it if it is missing.

### What you get
- `filename`: the name the *client* supplied. It is untrusted and can contain path separators.
- `content_type`: the part's declared MIME type, also untrusted.
- `size`: bytes received (on recent Starlette versions).
- `headers`: the part's headers.
- `file`: the underlying `SpooledTemporaryFile`, a synchronous file object.
- `await read(n)`, `await write(data)`, `await seek(offset)` and `await close()` are async wrappers
  that run the underlying sync file operation in a thread pool when the data is on disk. That way a
  disk read never blocks the event loop.

### When the bytes arrive
The whole multipart body is parsed **before** your handler runs. By the time your code sees
`UploadFile`, the upload is complete and sitting in memory or a temp file. That has two consequences.
First, a size check inside the handler protects downstream steps (parsing, storage) but not the
upload itself, so caps belong in middleware or at the proxy. Second, temp files use the container's
temp directory, which on many platforms is small or memory-backed, so large uploads can fill it.

### Lifetime
An `UploadFile` is request-scoped: FastAPI closes it, and the temp file is deleted, when the request's
lifecycle ends. Exactly when has changed between releases. Some FastAPI versions close upload files as
soon as the endpoint returns, *before* `BackgroundTasks` run, so a task that reads the object hits
"I/O operation on closed file". Current versions close them only after the response and its
background tasks finish. Don't depend on either: a thread or task that outlives the request, a job
queue, or a worker in another process can never use the object safely. Read the bytes, or copy to
durable storage, inside the handler, and pass a path or object key onward.

### JSON plus files
A request is either JSON or multipart, not both. To send metadata alongside a file, use `Form()`
fields, or one form field containing a JSON string that you validate with a Pydantic model's
`model_validate_json`. Since FastAPI 0.113 you can also bind a Pydantic model to the form fields
themselves (`meta: Annotated[Meta, Form()]`), which suits flat options.

### Alternatives
`bytes = File()` is fine for small, bounded files. Streaming the raw body via `request.stream()` (for
single-file `application/octet-stream` uploads) avoids multipart and temp files entirely and lets you
pipe bytes straight to object storage. Presigned direct-to-storage uploads take large files off your
API altogether.

## Example
Accepting an audio file plus metadata, copying it to storage in chunks without loading it whole, and
handing a key to a transcription job.

```python
import hashlib, uuid
from pydantic import BaseModel
from fastapi import FastAPI, Form, HTTPException, UploadFile

app = FastAPI()
CHUNK = 1 << 20                       # 1 MiB
MAX = 200 * (1 << 20)                 # downstream cap; edge/middleware caps the upload itself

class Meta(BaseModel):
    language: str = "en"
    diarize: bool = False

@app.post("/v1/transcriptions", status_code=202)
async def transcribe(audio: UploadFile, meta: str = Form("{}")):
    m = Meta.model_validate_json(meta)
    if not (audio.content_type or "").startswith("audio/"):
        raise HTTPException(415, "audio file required")     # also sniff magic bytes in practice
    key = f"uploads/{uuid.uuid4()}"                          # never reuse audio.filename as a path
    digest, total = hashlib.sha256(), 0
    with open(f"/data/{key.split('/')[1]}", "wb") as out:    # stand-in for object storage
        while chunk := await audio.read(CHUNK):
            total += len(chunk)
            if total > MAX:
                raise HTTPException(413, "audio too large")
            digest.update(chunk)
            out.write(chunk)
    # enqueue(transcribe_job, key=key, lang=m.language)       # pass the key, not the UploadFile
    return {"job_id": key, "bytes": total, "sha256": digest.hexdigest(),
            "original_name": audio.filename}
```

A client sends it with `curl -F "audio=@call.m4a;type=audio/mp4" -F 'meta={"language":"de"}'`. A
150 MiB recording is spooled to temp disk by the parser, then copied in 150 one-MiB reads, so the
handler's memory stays around a megabyte. The SHA-256 lets you deduplicate repeat uploads. The real
file write uses a sync `open`, which in production should be async object-storage I/O or run in a
thread.

## Trade-offs
- `UploadFile` bounds memory by spooling to disk, but it costs a temp-file write and read. For tiny
  files `bytes = File()` is simpler and just as good, and the choice flips at roughly "could many of
  these in parallel hurt memory?"
- Multipart is universally supported by browsers and HTTP tools, but it forces the whole body through
  your API process before your code runs. Raw streaming or presigned uploads win once files are large
  or uploads are frequent.
- Accepting many files in one request (`list[UploadFile]`) is convenient for "upload a folder", but one
  bad file fails the whole request. Per-file requests or per-file job status give clearer partial
  failures.
- Validating inside the handler is easy to write, but the bytes have already been received. It
  protects downstream systems, not the API's own memory, disk and bandwidth.

## Pitfalls
- Using `bytes = File()` for large media. The symptom is worker memory climbing with concurrent uploads
  until pods are OOM-killed under modest load.
- Passing the `UploadFile` to a background task or another thread. The symptom is "I/O operation on
  closed file" errors that appear only in the background job, or code that works until a FastAPI
  upgrade changes when upload files are closed, or until the work moves to a queue.
- Writing to disk at `audio.filename`. The symptom is files landing outside the intended directory or
  overwriting each other, which is a path-traversal vulnerability.
- Calling `audio.file.read()` (sync) on a large spooled file inside an `async def` handler. The symptom
  is the event loop stalling and other requests' latency spiking during uploads.
- Forgetting to `seek(0)` after sniffing the first bytes. The symptom is stored files missing their
  first few bytes and failing to open.
- Trying to declare a Pydantic JSON body next to an `UploadFile`. The symptom is a 422 complaining
  about the body, because the request is multipart, not JSON.

## When to use
Use `UploadFile` for any user-facing upload of documents, images or audio that might be larger than a
few megabytes or arrive in parallel. Use `bytes = File()` only for small, capped payloads. Switch to
raw `request.stream()` or presigned direct-to-storage uploads for large media, high upload volume, or
when you want bytes going straight to object storage without a temp file. Pair it with size limits
outside the handler and content checks inside it, and pass storage keys rather than file objects to
async work. Watch temp-disk usage, upload latency and worker memory. Interviewers expect you to know
the spooled-file behaviour and that parsing completes before the handler runs.

## Role lens
- **Engineer:** Declares `UploadFile` with `Form` metadata, streams it to storage in chunks with async
  reads, and passes storage keys to ingestion or transcription jobs.
- **Architect:** Decides whether files flow through the API at all or go direct to object storage, and
  sizes temp volumes and worker memory for peak concurrent uploads.
- **FDE:** Gets a customer's scanner or call-recording system to post multipart correctly, often
  fixing missing filenames, wrong content types or proxy body limits on their side.

## Check yourself
**Q:** You check `if audio.size > 100_000_000: raise HTTPException(413)` at the top of the handler, yet a
1 GB upload still fills the pod's temp disk and slows other requests. Why, and what should change?
**A:** By the time the handler runs, Starlette has already parsed the whole multipart body and spooled
the 1 GB file to a temporary file. The check rejects the request after the bandwidth, disk and parsing
work have been spent. It protects downstream steps but not the upload itself. The cap must come before
parsing: `client_max_body_size` or the gateway limit at the edge, plus ASGI middleware that rejects on
`Content-Length` and counts streamed bytes to stop chunked uploads. For genuinely large media, use
presigned uploads straight to object storage, so the API only receives a key. Make sure temp space is
on a properly sized volume.

## Quiz
### Q: What does `UploadFile` do with a large file part that `bytes = File()` does not?
- [ ] It compresses the file in memory
- [x] It spools the part to a temporary file once it passes a size threshold, keeping memory bounded
- [ ] It streams the file to the handler before the rest of the body arrives
- [ ] It validates the file type with magic bytes
> UploadFile wraps a SpooledTemporaryFile that rolls over to disk, so memory stays small. bytes = File()
> materialises the whole part in RAM. Neither validates type or streams to the handler early.

### Q: Why should a handler persist an upload and pass a storage key to follow-up work, rather than pass the `UploadFile` object?
- [ ] Background tasks cannot perform file I/O
- [ ] An `UploadFile` is always empty once the handler has returned
- [x] The object wraps a request-scoped temp file that FastAPI closes and deletes when the request lifecycle ends, sometimes before BackgroundTasks run
- [ ] Background tasks run before the multipart body is parsed
> The temp file belongs to the request. Depending on the FastAPI version it may already be closed when a
> background task runs, and it can never reach a queue worker or survive a crash. A path or object key can.

### Q: A client needs to send a PDF together with structured options. What is the correct FastAPI approach?
- [ ] Declare a Pydantic model body and an UploadFile in the same handler as JSON
- [ ] Base64-encode the PDF inside a JSON body and use UploadFile anyway
- [x] Send multipart with the file plus Form fields (or one JSON-string form field validated by Pydantic)
- [ ] Send the PDF in a header and the options in the body
> A request body has one encoding. Files require multipart, so metadata must travel as form fields, and
> a JSON-string field validated with model_validate_json keeps it structured and typed.
