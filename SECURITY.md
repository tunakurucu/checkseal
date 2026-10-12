# Security and trust boundaries

Checkseal is a local developer convenience, not a sandbox or security attestation system. It runs explicitly requested executables with the caller's permissions. It cannot protect a repository against a malicious command or agent that can write the repository or its Git metadata.

Fingerprinting reads tracked and nonignored untracked files. Source bytes and child output are not stored in receipts. Commands, command arguments, file paths, hashes and timing information are stored locally. Avoid putting secrets in arguments. Review reports before publishing them.

Reports escape text, contain no JavaScript, and set a restrictive content security policy. Git operations during inspection are read operations and do not run repository hooks. The tool never automatically sends a report or opens a browser.

Do not post private source or exploitable sensitive details in public issues. Report vulnerabilities through GitHub private vulnerability reporting if it is enabled. Otherwise open an issue requesting a private contact route without the sensitive details.

The v0.1 line is the only supported version line. See README for unsupported repository layouts and evidence limitations.
