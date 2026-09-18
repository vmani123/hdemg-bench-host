.PHONY: test sim report parity effort fw-s3 fw-c5 clean
PY ?= python3

test:                     ## loopback self-test — the whole harness, no hardware
	cd host && $(PY) -m pytest tests/ -q

sim:                      ## fast end-to-end simulated sweep + report
	cd host && $(PY) -m cli.bench sim --matrix matrices/stage1.yaml --hold 1.2 \
		--ledger results/sim.jsonl
	cd host && $(PY) -m cli.bench report --ledger results/sim.jsonl --out results/sim-report

report:                   ## render figures + tables from the real ledger
	cd host && $(PY) -m cli.bench report

parity:                   ## shared-core integrity check
	cd host && $(PY) -m cli.bench parity --firmware ../firmware

effort:                   ## equal-effort protocol check (plan §5.4)
	cd host && $(PY) -m cli.bench effort

fw-s3:                    ## build the S3 firmware (needs ESP-IDF)
	cd firmware/esp32s3 && idf.py set-target esp32s3 && \
		idf.py -DBENCH_INGRESS=synth -DBENCH_RUNG=r0-baseline build

fw-c5:                    ## build the C5 firmware (needs ESP-IDF)
	cd firmware/esp32c5 && idf.py set-target esp32c5 && \
		idf.py -DBENCH_INGRESS=synth -DBENCH_RUNG=r0-baseline build

clean:
	rm -rf host/results/sim.jsonl host/results/sim-report host/results/report \
		firmware/*/build firmware/*/sdkconfig firmware/*/sdkconfig.rung
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	rm -rf host/.pytest_cache
