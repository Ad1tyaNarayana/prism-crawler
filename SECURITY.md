# Security policy

Prism is an early-stage local library. It is not an isolation boundary for an untrusted multi-tenant service. Only the latest development version receives fixes; there is no LTS line yet.

## Reporting

Report suspected vulnerabilities privately through [GitHub security advisories](https://github.com/Ad1tyaNarayana/prism-crawler/security/advisories/new). If private reporting is unavailable, open an issue asking for a private reporting channel without publishing exploit details or sensitive URLs. The maintainer will arrange a channel. Do not include credentials, cookies or private crawled content in public issues.

Useful reports include an affected revision, a local reproducer, expected and actual behavior, and the boundary crossed. There is no bug-bounty program or guaranteed response time.

## Threat model

Remote page HTML, JavaScript and structured data are untrusted. The default network layer rejects private destinations, restricts origins, checks robots, and bounds resources. Chromium requests are intercepted and fulfilled through that transport, with a deny-only proxy for uninstrumented targets. These controls have behavioral tests but have not received an independent security audit.

`allow_private=True` authorizes private/local destinations for trusted environments. It should not be enabled for arbitrary user-submitted URLs. Asset and redirect origin allowlists expand scope and must be chosen deliberately. The crawler does not automatically interpret site terms.

Run Chromium and PDF processing with OS-level isolation appropriate to your environment. Do not expose this library directly to anonymous users. Keep dependencies and browser binaries updated. A robots policy is not a defense against malicious source code.

SQLite databases and exports retain source text, request metadata and URLs. Treat them as potentially sensitive. Raw benchmark captures, API keys, provider state and model downloads are excluded from this repository. Optional remote decision endpoints receive bounded snippets when explicitly configured.
