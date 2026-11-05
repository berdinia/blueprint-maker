# Known Issues

Generic software defects. No private examples are included.

## Critical

### K1: Proxy probe timeout causes silent cloud fallback
When the proxy endpoint times out, the extraction runner may silently
fall back to the native endpoint or, in the original workflow, to a
remote cloud URL. This must be prevented. The runner should block the
job cleanly and log the reason, never redirect.

**Fix:** Replace silent fallback with an explicit `BLOCKED` result and
structured error. See `extraction.py: _invoke_model`.

### K2: Stale lock detection is platform-dependent
The stale-lock detection uses `/proc/<pid>` on Linux but falls back to
`os.kill(pid, 0)` on macOS. This is incomplete: stale detection may
fail to reclaim a lock after a process crash.

**Fix:** Use a `fcntl.flock` or `portalocker` for cross-platform
advisory locking.

### K3: DXF text collision detection is too coarse
The current text collision detection uses Euclidean distance. It does
not account for text extent (font size, anchor, alignment).

**Fix:** Use `ezdxf` text bounds to compute actual collision.

## High

### K4: No room overlap detection
Overlapping room rectangles are not detected. Two rooms may share the
same space without warning.

**Fix:** Add an `AABB` overlap check between all room rectangles.

### K5: Openings without positions are not placed
This is by design, but the current logic is fragile. There is no clear
separation between "recorded but not placed" and "placed".

**Fix:** Introduce a `placement` enum: `NOT_KNOWN`, `ESTIMATED`,
`PLACED`, `REVIEWED`.

### K6: No property-based tests for rendering transform
The renderer's bounding-box transform is not tested with a property-based
approach (e.g., `hypothesis` or `pytest-quickcheck`).

**Fix:** Add a `tests/test_renderer_properties.py` that generates
random room layouts and verifies invariant: geometry is always inside
the drawable area.

## Medium

### K7: Source registry is not versioned
The registry format is unversioned. Future schema changes may break
backward compatibility.

**Fix:** Add a `$schema` field and a `$version` field to the registry
and all extracted data.

### K8: No async extraction
Extraction is synchronous. For multiple sources, a thread or async
runner would improve throughput.

**Fix:** Add `ExtractionRunner` async support using `asyncio` or
`concurrent.futures.ThreadPoolExecutor`.

## Low

### K9: Documentation may carry stale references
Some docstrings or examples may reference obsolete path or project names.

**Fix:** Define a private sensitive-term list (not shipped in the export) and
grep the export for each term to identify and replace any stale references.
Do not hard-code real project or source names in shipped example commands.

### K10: No Dockerfile or CI
The pipeline has no container image or CI pipeline.

**Fix:** Add a `Dockerfile` and a GitHub Actions workflow for local
testing.
