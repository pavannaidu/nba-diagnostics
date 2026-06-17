TARGET ?= fevm
PROFILE ?= FEVM

.PHONY: frontend-build validate deploy bootstrap custom-agent-run app-run smoke cleanup-legacy full-deploy py-compile

frontend-build:
	npm --prefix app/frontend run build

validate:
	databricks bundle validate -t $(TARGET)

deploy: frontend-build validate
	databricks bundle deploy -t $(TARGET)

bootstrap:
	databricks bundle run bootstrap_runtime_assets -t $(TARGET)

app-run:
	databricks bundle run nba_diagnostics_app -t $(TARGET)

custom-agent-run:
	databricks bundle run custom_diagnostic_agent -t $(TARGET)

smoke:
	databricks bundle run smoke_test_runtime -t $(TARGET)

cleanup-legacy:
	PYTHONPATH=scripts python scripts/cleanup_legacy_uc_assets.py --profile $(PROFILE) --execute

full-deploy: deploy bootstrap custom-agent-run app-run smoke

py-compile:
	python -m py_compile app/app.py app/backend/*.py scripts/*.py agent_app/agent_server/*.py
