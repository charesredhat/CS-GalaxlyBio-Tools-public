---
name: galaxy-test
description: Run Galaxy tool tests using bioblend
---

# Galaxy Tool Testing

Run the MicroRunQC test suite against the Galaxy instance.

## Steps

1. Check that environment variables are set:
   - `GALAXY_URL`
   - `GALAXY_API_KEY`

#2. Run the test script:
#   ```bash
#   python complete_microrunqc_test.py
#   ```

2. Report results and any failures found

## On failure

- Check Galaxy server connectivity
- Verify API key is valid
- Review test output for specific errors
