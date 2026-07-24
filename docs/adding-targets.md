# Adding a monitored target

A target is one entry in [`targets.yaml`](../targets.yaml). Each night
`runner/run.py` runs `provenance-probe assess` against it as a **black-box CLI**
(the engine is never imported), diffs the result against a pinned baseline for
drift, and writes a two-tier record to `data/<target>/<date>/verdict.json`.

This covers the observatory-specific wrapping: the gating fields, secrets, and
the neutral-only / Gate-1 posture. For the endpoint mechanics themselves
(OpenAI/Anthropic styles, the `template` web-app adapter, the full field
reference, troubleshooting), see the engine guide:
**[provenance-probe `docs/adding-sources.md`](https://github.com/lobster-shrimp/provenance-probe/blob/main/docs/adding-sources.md)**.

---

## The gating model (read first)

Three fields decide whether a target is probed and whether its verdict may be
published. They are deliberately conservative — a new target is inert until you
opt in on purpose.

| Field | Meaning | Default for a new target |
|---|---|---|
| `kind` | `control-positive` / `control-negative` (your own endpoints) vs anything else (`aggregator`, `first-party`, `cn-direct`, `webapp` = "commercial") | set honestly |
| `authorized` | scope attestation — you are authorized in writing to probe it | **`false`** |
| `public` | may the interpreted verdict be promoted to the public feed | **`false`** |

Plus one environment gate:

- **Controls** (`kind` starts with `control`) run whenever `authorized: true`.
- **Everything else** runs only when `OBSERVATORY_PROBE_COMMERCIAL=1` **AND**
  `authorized: true`. So no named vendor is touched before you flip both.

`public: false` means the neutral evidence (token counts, wire, latency, drift,
`fingerprint_id`) is still logged, but the interpreted provenance/jurisdiction
verdict is **withheld** on the site until a promoted advisory clears it
(two-tier publication, T5). **Keep `public: false` for any named vendor until
Gate 1 (counsel) clears it** — publishing an interpreted named-vendor verdict is
the legal exposure the whole gate exists to prevent. Controls are about your own
endpoints, so their `control_check` is always publishable.

Nightly scope is **neutral**: the behavioral and deception layers are OFF for
commercial targets (U1). Run those point-in-time from the engine's local UI with
authorization, not in the public nightly.

---

## Adding an API target

```yaml
- name: some-aggregator-endpoint
  kind: aggregator                 # commercial -> needs COMMERCIAL=1 + authorized
  base_url: "https://api.vendor.example/v1"
  model: "MODEL_ID"
  api_style: openai                # or: anthropic
  auth_env: "SOME_API_KEY"         # env var / Actions secret holding the key
  public: false                    # withhold verdict until Gate 1
  authorized: false                # flip only with written authorization
  notes: "why this target is watched"
```

The runner maps `auth_env` to the engine's `auth_value_env`. Provide the key as
an environment variable locally and as an **Actions secret** in CI (see below).

## Adding a web-app target (`api_style: template`)

The runner passes the web-app fields through to the engine, so a browser chat
app works end-to-end. Capture a real request (DevTools → Network) and describe
it — full field semantics are in the engine's `docs/adding-sources.md`.

```yaml
- name: some-webapp
  kind: webapp                     # commercial -> needs COMMERCIAL=1 + authorized
  base_url: "https://chat.vendor.example"
  chat_path: "/api/v1/chat"
  models_path: "/api/v1/models"
  api_style: template
  cookie_env: "SOME_COOKIE"        # session cookie via env / Actions secret
  request_template:
    model: "the-model"
    messages: [{role: user, content: "__PROMPT__"}]
    max_tokens: "__MAX_TOKENS__"
    temperature: "__TEMPERATURE__"
  response_text_path: "choices.0.message.content"
  response_prompt_tokens_path: "usage.prompt_tokens"
  response_model_path: "model"
  public: false
  authorized: false
  notes: "web app; expect degraded coverage if usage is suppressed"
```

The runner forwards `chat_path`, `models_path`, `cookie_env`,
`request_template`, `response_*_path`, and `stream_*`. Web apps commonly suppress
`usage.prompt_tokens`, so the tokenizer layer is unavailable and drift runs on
wire + latency at **degraded confidence** — the site labels this per target and
the drift verdict carries a matching `confidence: degraded`. See the shipped
`chat-z-ai-webapp` entry for a worked, fully-gated example.

---

## Secrets

Never commit keys or cookies. Provide them as environment variables the runner
reads by the name in `auth_env` / `cookie_env`:

- **Locally:** export them (e.g. from `~/.zshrc`) before running the runner.
- **In CI:** add a repository **Actions secret** and reference it in the env
  block of `.github/workflows/observatory.yml`, e.g.:

  ```yaml
  SOME_API_KEY: ${{ secrets.SOME_API_KEY }}
  ```

  Set the secret in repo settings yourself; the workflow only references it.

---

## Test a target before trusting it

```bash
# controls false-positive self-test (Gate-2) against real GGUF tokenizers:
PROVENANCE_PROBE_SRC=../provenance-probe ./scripts/controls-selftest.sh

# a full local run against your targets file (commercial gate on, private dirs):
OBSERVATORY_PROBE_COMMERCIAL=1 \
OBSERVATORY_DATA_DIR=/tmp/obs-data OBSERVATORY_STAGING_DIR=/tmp/obs-staging \
  python runner/run.py --targets targets.yaml
```

Confirm `data/<target>/<date>/verdict.json` was written, that the interpreted
`verdict` block is absent while `public: false` (withheld), and — for a control
— that `control_check.pass` is true.

---

## Checklist for a new named-vendor target

- [ ] `authorized: false` and `public: false` in the committed file.
- [ ] Written authorization to probe the service obtained before flipping `authorized`.
- [ ] Secret (`auth_env` / `cookie_env`) added to Actions + referenced in the workflow.
- [ ] Gate 1 counsel clearance obtained before setting `public: true`.
- [ ] For web apps: a real captured `request_template` (not the scaffold), and an
      awareness that coverage may be degraded.
