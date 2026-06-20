#!/bin/bash
set -e
echo "Starting GCSFuse conformance integration tests..."
cd ~/gcsfuse
/usr/local/go/bin/go test -p 1 -v $(/usr/local/go/bin/go list ./tools/integration_tests/... | grep -v emulator_tests) --integrationTest --testbucket=kislayk-npi-gce-usc1 -timeout=60m > ~/integration_tests.log 2>&1
echo $? > ~/conformance.exit
echo "Conformance tests finished with exit code $(cat ~/conformance.exit)"
