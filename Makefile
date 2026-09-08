studio:
	langgraph dev

frontend-install:
	cd frontend && npm install

frontend-codegen:
	PYTHONPATH=. .venv/bin/python -m scripts.export_openapi
	cd frontend && npm run codegen

frontend-typecheck:
	cd frontend && npm run typecheck

frontend-test:
	cd frontend && npm run test

frontend-build:
	cd frontend && npm run build

frontend-dev:
	cd frontend && npm run dev

test-all:
	.venv/bin/python -m pytest
	cd frontend && npm run test
