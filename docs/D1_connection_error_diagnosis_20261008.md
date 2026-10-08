# D1 RelayRouter connection-error diagnosis — 2026-10-08

## Scope and source

Read-only review of `logs/mechanism_gate_d1_v1_20261008T103459Z.jsonl` and its already-produced summary. No API request was made and the original D1 analyzer was not rerun.

## Observed facts

- The JSONL contains 26 attempt records: 14 completed source slots and 12 `APIConnectionError` records from six source slots (run indices 15–20).
- All 12 errors are clustered from `2026-10-08T10:36:20.567470Z` to `2026-10-08T10:36:24.112647Z`, a span of about 3.55 seconds. Each of the six final source slots has one initial attempt and one retry; both failed.
- Every record reports `status_code: null`. The error record contains only the exception type, status code, and failed round. It does not preserve the underlying exception message, request ID, response headers, DNS/connect phase, or socket error.
- For the first failed source slot (run index 15), the model completed one response and received the A2A Artifact before the second model request failed. For run indices 16–20, both attempts failed on their first model request; their round-latency arrays are empty.
- No HTTP 429, 5xx, or MCP error is evidenced in these JSONL records. The record format cannot distinguish a RelayRouter transient, a local network interruption, or another transport-layer failure.

## Diagnosis and limits

The supported diagnosis is **a short, time-clustered transport/API connection failure**, not a confirmed rate-limit event and not a confirmed RelayRouter outage. The 3.55-second cluster after 14 completed slots is compatible with a transient route interruption, but the surviving fields do not identify where the connection failed. The official RelayRouter error-code documentation distinguishes 429 rate limiting from 5xx upstream/gateway responses; this batch has no status code with which to apply that distinction ([RelayRouter Error Codes](https://relayrouter.io/docs/errors)). A verifiable incident-history record for the exact UTC window was not available during this audit.

## Frozen retry rule for E1 and D1b

- Disable SDK automatic retries (`max_retries=0`) and log each attempt as a separate JSONL row.
- For `APIConnectionError`/other status-less transport failures, HTTP 429, or HTTP 5xx, allow at most **two retries per source slot** after the initial attempt (three total attempts), using the two preallocated retry IDs and waits of 5 seconds then 15 seconds.
- Do not retry other 4xx responses; stop the batch on these deterministic client/configuration errors.
- A completed turn or any observed canary write is terminal for that source slot. Do not repeat it after a later-round transport error. After the two retries are exhausted, retain the unresolved slot as invalid and continue the frozen schedule; no result-based replacement is permitted.
- Preserve the exception class, status code, failed round, elapsed time, and response/request identifiers when available. Never log credentials or full authorization headers.

This rule is based on the observed clustering and missing diagnostic fields; it does not claim to have identified the provider-side root cause.
