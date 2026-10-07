.PHONY: build check audit
build:
	python3 scripts/build_reference.py --input-directory docs/reference/demo --output-directory dist
check:
	python3 scripts/build_reference.py --input-directory docs/reference/demo --output-directory dist --check
audit:
	python3 scripts/build_reference.py --audit
