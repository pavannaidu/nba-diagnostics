# Development

## Local Build

Build the frontend into the Databricks App static directory:

```bash
make frontend-build
```

Run the FastAPI app locally after building static assets:

```bash
uvicorn app:app --app-dir app --reload
```

The Vite dev server can still be used during UI work:

```bash
npm --prefix app/frontend run dev
```

## DAB Workflow

The bundle is the supported deployment interface:

```bash
make deploy TARGET=fevm
make bootstrap TARGET=fevm
make custom-agent-run TARGET=fevm
make app-run TARGET=fevm
make smoke TARGET=fevm
```

For the full path:

```bash
make full-deploy TARGET=fevm
```

## Debugging

Useful checks:

```bash
make py-compile
npm --prefix app/frontend run build
databricks bundle validate -t fevm
databricks bundle run smoke_test_runtime -t fevm
```

The app runtime should fail visibly with `status="unavailable"` if the selected agent target, UC tools, Genie, or Knowledge Assistant path fails. It should not silently fall back to deterministic heuristic recommendations. If the custom agent app URL is missing from `runtime_config`, the UI keeps that target disabled.

## Legacy UC Cleanup

After bootstrap, remove unused legacy/debug functions with a dry run first:

```bash
PYTHONPATH=scripts python scripts/cleanup_legacy_uc_assets.py --profile FEVM
PYTHONPATH=scripts python scripts/cleanup_legacy_uc_assets.py --profile FEVM --execute
```

The script protects the active runtime functions and any UC function currently attached to the Supervisor.
