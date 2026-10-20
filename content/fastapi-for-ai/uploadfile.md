---
id: uploadfile
module: fastapi-for-ai
name: UploadFile
kind: tool
difficulty: intro
est_minutes: 12
status: crisp
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
`UploadFile` is FastAPI's type for `multipart/form-data` files (PDFs, images, audio). It wraps a spooled
temporary file, in memory while small and on disk when large, with filename, content type and async
read/seek. Prefer it to `bytes = File()`; it does no size or safety checks.

## Why it exists
- **Files arrive as multipart.** Browsers and HTTP clients send a body split into parts by a boundary
  string, each part with its own headers (`Content-Disposition` with field name and filename,
  `Content-Type`) and raw bytes.
- **JSON is a poor carrier.** It can't carry binary data efficiently, and base64 inflates it by a third.
  So an AI service that ingests documents, screenshots or voice notes must parse multipart.
- **`bytes = File()` holds the whole part in memory.** Fine for a 2 KB avatar. For a 300 MB audio file it
  means 300 MB per concurrent request held until the handler ends; ten simultaneous uploads can OOM-kill
  a pod.
- **Reading `request.stream()` yourself** avoids the memory cost but means writing your own multipart parser.
- **`UploadFile` sits in between.** Starlette's parser streams each file part into a
  `SpooledTemporaryFile`, which stays in memory below a threshold (1 MiB in current Starlette) and rolls
  over to disk above it: a file-like object with metadata, bounded memory, and no parser to write.

## How it works
### Declaring it
| Need | Declaration |
|---|---|
| one required file field named `file` | `async def ingest(file: UploadFile)` |
| several files | `files: list[UploadFile]` |
| optional file | `UploadFile | None = None` |
| regular form fields | `Form()`, e.g. `title: str = Form()` |

- **Multipart support requires the `python-multipart` package.** FastAPI raises an error at startup that
  names it if it is missing.

### What you get
| Attribute / method | Meaning |
|---|---|
| `filename` | the name the *client* supplied; untrusted and can contain path separators |
| `content_type` | the part's declared MIME type; also untrusted |
| `size` | bytes received (on recent Starlette versions) |
| `headers` | the part's headers |
| `file` | the underlying `SpooledTemporaryFile`, a synchronous file object |
| `await read(n)`, `await write(data)`, `await seek(offset)`, `await close()` | async wrappers that run the sync file operation in a thread pool when the data is on disk, so a disk read never blocks the event loop |

### async def or def
The handler type decides which API is safe:
- **`async def`: use the awaited methods.** Touching `file.file` directly does blocking disk I/O on the
  event loop.
- **Plain `def`: use the sync object.** FastAPI already runs your code in a thread pool, so
  `shutil.copyfileobj(upload.file, dest)` copies a spooled file in bounded-size chunks without any `await`.
- **When `def` is simpler:** everything it does with the file (hashing, local copies, a sync storage SDK)
  is blocking anyway. The cost is one thread-pool slot per in-flight upload, and that pool is small by
  default.

### When the bytes arrive
The whole multipart body is parsed **before** your handler runs. By the time your code sees
`UploadFile`, the upload is complete and sitting in memory or a temp file. Two consequences:
1. **A size check inside the handler protects downstream steps** (parsing, storage) but not the upload
   itself, so caps belong in middleware or at the proxy.
2. **Temp files use the container's temp directory**, which on many platforms is small or memory-backed,
   so large uploads can fill it.

### Lifetime
- **An `UploadFile` is request-scoped.** FastAPI closes it, and the temp file is deleted, when the
  request's lifecycle ends.
- **Exactly when has changed between releases.** Some FastAPI versions close upload files as soon as the
  endpoint returns, *before* `BackgroundTasks` run, so a task that reads the object hits "I/O operation on
  closed file". Current versions close them only after the response and its background tasks finish.
- **Don't depend on either.** A thread or task that outlives the request, a job queue, or a worker in
  another process can never use the object safely.
- **So:** read the bytes, or copy to durable storage, inside the handler, and pass a path or object key onward.

### JSON plus files
- **A request is either JSON or multipart, not both.**
- **Metadata options:** `Form()` fields, or one form field containing a JSON string that you validate with
  a Pydantic model's `model_validate_json`.
- **Since FastAPI 0.113** you can also bind a Pydantic model to the form fields themselves
  (`meta: Annotated[Meta, Form()]`), which suits flat options.

### Alternatives
| Option | Use when |
|---|---|
| `bytes = File()` | small, bounded files |
| raw body via `request.stream()` | single-file `application/octet-stream` uploads; avoids multipart and temp files and pipes bytes straight to object storage |
| presigned direct-to-storage uploads | large files; takes them off your API altogether |

## Examples
### Audio upload plus metadata, copied in chunks, handed to a job
Copies the file to storage without loading it whole, and hands a key to a transcription job.

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

A client sends it with `curl -F "audio=@call.m4a;type=audio/mp4" -F 'meta={"language":"de"}'`.

### Worked trace: the bytes of a 150 MiB recording
| Stage | Where the bytes are | Peak memory for the part |
|---|---|---|
| request still arriving | Starlette's parser writes the part into a `SpooledTemporaryFile`; after the first 1 MiB it rolls over to the temp directory | around a megabyte, while 150 MiB lands on temp disk |
| handler starts (only now) | copies the file in 150 one-MiB reads to the destination | around a megabyte |
| after the copy | written to disk twice (temp file and destination) | — |
| same request with `bytes = File()` | all 150 MiB in a single `bytes` object for the life of the handler | 150 MiB |

- **The double write** is the cost a presigned direct upload avoids.
- **The SHA-256** lets you deduplicate repeat uploads.
- **The real file write uses a sync `open`**, which in production should be async object-storage I/O or
  run in a thread.

### Several files in a plain `def` handler (runnable with TestClient)
Requires `fastapi`, `httpx` and `python-multipart`. Shows `list[UploadFile]`, `Form` fields, the sync
copy, and that the client's filename arrives untouched.

```python
import os, shutil, tempfile, uuid
from fastapi import FastAPI, Form, UploadFile
from fastapi.testclient import TestClient

app = FastAPI()
STORE = tempfile.mkdtemp()                     # stand-in for durable object storage

@app.post("/v1/batch", status_code=202)
def batch(files: list[UploadFile], language: str = Form("en"),
          diarize: bool = Form(False)):        # plain def: FastAPI runs it in a thread pool
    out = []
    for f in files:
        key = uuid.uuid4().hex                 # generated name; f.filename is only metadata
        with open(os.path.join(STORE, key), "wb") as dest:
            shutil.copyfileobj(f.file, dest)   # sync copy in bounded chunks, no await needed
        out.append({"key": key[:8], "name": f.filename, "size": f.size, "type": f.content_type})
    return {"language": language, "diarize": diarize, "files": out}

c = TestClient(app)
r = c.post("/v1/batch",
           files=[("files", ("a.wav", b"\0" * (3 << 20), "audio/wav")),   # 3 MiB: spooled to disk
                  ("files", ("../b.wav", b"RIFF", "audio/wav"))],          # hostile name, kept as text
           data={"language": "de", "diarize": "true"})
print(r.status_code, r.json()["diarize"], [(f["name"], f["size"]) for f in r.json()["files"]])
# 202 True [('a.wav', 3145728), ('../b.wav', 4)]
print(c.post("/v1/batch", json={"language": "de"}).status_code)   # 422: JSON is not multipart
shutil.rmtree(STORE)
```

## Production patterns
- **Temp space on a sized volume** — whenever uploads exceed a few megabytes. Point `TMPDIR` (which
  Python's `tempfile`, and so the spooled files, honour) at a disk-backed volume, for example a Kubernetes
  `emptyDir` with a `sizeLimit`. Monitor temp-disk usage per pod and evictions for ephemeral storage.
- **Persist in the handler, pass a key** — for every upload that feeds async work. Copy to object storage
  inside the request (`await asyncio.to_thread(s3.upload_fileobj, upload.file, bucket, key)` with boto3),
  then enqueue the key. Monitor jobs that fail to find their object.
- **Content-hash deduplication** — for RAG corpora where users re-upload the same files. Hash while
  copying, look up the SHA-256 per tenant, and skip re-parsing and re-embedding on a hit. Monitor
  dedup hit rate and embedding spend saved.
- **Raw-stream route for single large files** — for audio or video from trusted clients. Accept
  `application/octet-stream` and `async for chunk in request.stream()` straight into a multipart
  object-storage upload, skipping the temp file. Monitor upload throughput and incomplete uploads.
- **Upload concurrency budget** — when `def` handlers do the copying. Size the thread pool and worker
  memory for peak concurrent uploads, and cap concurrent uploads per tenant. Monitor thread-pool
  saturation, upload latency and worker memory.

## Trade-offs
- **`UploadFile` vs `bytes = File()`.** `UploadFile` bounds memory by spooling to disk but costs a
  temp-file write and read. For tiny files `bytes = File()` is simpler and just as good; the choice flips
  at roughly "could many of these in parallel hurt memory?"
- **Multipart vs raw streaming or presigned uploads.** Multipart is universally supported by browsers and
  HTTP tools, but forces the whole body through your API process before your code runs. Raw streaming or
  presigned uploads win once files are large or uploads are frequent.
- **Many files per request vs one.** `list[UploadFile]` is convenient for "upload a folder", but one bad
  file fails the whole request. Per-file requests or per-file job status give clearer partial failures.
- **Validating in the handler vs before it.** In-handler checks are easy to write, but the bytes have
  already been received. They protect downstream systems, not the API's own memory, disk and bandwidth.

## Pitfalls
- **`bytes = File()` for large media.** Symptom: worker memory climbs with concurrent uploads until pods
  are OOM-killed under modest load.
- **Passing the `UploadFile` to a background task or another thread.** Symptom: "I/O operation on closed
  file" errors only in the background job, or code that works until a FastAPI upgrade changes when upload
  files are closed, or until the work moves to a queue.
- **Writing to disk at `audio.filename`.** Symptom: files land outside the intended directory or
  overwrite each other, a path-traversal vulnerability.
- **Sync `audio.file.read()` on a large spooled file in an `async def` handler.** Symptom: the event loop
  stalls and other requests' latency spikes during uploads.
- **Forgetting `seek(0)` after sniffing the first bytes.** Symptom: stored files miss their first few
  bytes and fail to open.
- **A Pydantic JSON body next to an `UploadFile`.** Symptom: a 422 complaining about the body, because
  the request is multipart, not JSON.

## When to use
- **Use `UploadFile` for** any user-facing upload of documents, images or audio that might be larger than
  a few megabytes or arrive in parallel.
- **Use `bytes = File()` only for** small, capped payloads.
- **Switch to raw `request.stream()` or presigned uploads for** large media, high upload volume, or bytes
  going straight to object storage without a temp file.
- **Pair it with** size limits outside the handler and content checks inside it, and pass storage keys
  rather than file objects to async work.
- **Measure:** temp-disk usage, upload latency and worker memory.
- **Interviews:** know the spooled-file behaviour and that parsing completes before the handler runs.

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
