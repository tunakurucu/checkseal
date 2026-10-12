# Contributing

Start by running `python3 checkseal.py demo`, then `python3 -B -m unittest discover -s tests -v`.

Keep the runtime standard-library only and runnable as one file. Changes should preserve explicit coverage limits, fail closed when receipt data is invalid, and avoid executing anything during `check` except the Git read commands.

For a bug, include the OS, Python and Git versions, commands, actual status, expected status, and a minimal disposable repository that reproduces it. Do not include secrets, proprietary source, or complete private receipts.

Useful first contributions:

- Validate Windows behavior, especially process-tree cleanup and executable-bit semantics.
- Test receipt size and runtime on large real repositories.
- Design dependency/environment fingerprints with explicit privacy and reproducibility limits.

Before expanding platform support or freshness claims, add a behavioral test that demonstrates the promised behavior. Do not add a “security verified” badge or imply that local receipts resist forgery.

Open an issue before implementing a new feature with dependencies or a background service. Include what problem you observed in an actual workflow.
