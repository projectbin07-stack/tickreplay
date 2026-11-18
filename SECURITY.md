# Security policy

## Scope

tickreplay is an offline simulator. It opens no network connections, handles no
credentials, keys or wallets, and executes no code from its input files. The realistic
security surface is:

- parsing untrusted CSV files (`tickreplay.data.csvio`);
- writing HTML reports that embed strings taken from those files and from command-line
  arguments (`tickreplay.report`), which are HTML-escaped.

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository
(Security tab, "Report a vulnerability") rather than a public issue. Include a minimal
input that reproduces the problem. This is a personal open-source project maintained on
a best-effort basis; there is no guaranteed response time.

## Not a security issue

Simulated results that differ from real trading outcomes are a known limitation of any
backtester, not a vulnerability. See "Known limitations" in the README.
