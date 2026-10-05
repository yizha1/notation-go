# notation-go

[![Build Status](https://github.com/notaryproject/notation-go/actions/workflows/build.yml/badge.svg?event=push&branch=main)](https://github.com/notaryproject/notation-go/actions/workflows/build.yml?query=workflow%3Abuild+event%3Apush+branch%3Amain)
[![Codecov](https://codecov.io/gh/notaryproject/notation-go/branch/main/graph/badge.svg)](https://codecov.io/gh/notaryproject/notation-go)
[![Go Reference](https://pkg.go.dev/badge/github.com/notaryproject/notation-go.svg)](https://pkg.go.dev/github.com/notaryproject/notation-go@main)

notation-go contains libraries for signing and verification of artifacts as per [Notary Project specifications](https://github.com/notaryproject/specifications). notation-go is being used by [notation](https://github.com/notaryproject/notation) CLI for signing and verifying artifacts.

notation-go reached a stable release as of July 2023 and continues to be actively developed and maintained.

Please visit [README](https://github.com/notaryproject/.github/blob/main/README.md) to know more about Notary Project.

> [!NOTE]
> The Notary Project documentation is available [here](https://notaryproject.dev/docs/).

## Table of Contents

- [Documentation](#documentation)
- [Trusted identity escaping](#trusted-identity-escaping)
- [Code of Conduct](#code-of-conduct)
- [License](#license)
 
## Documentation

Library documentation is available at [Go Reference](https://pkg.go.dev/github.com/notaryproject/notation-go).

## Trusted identity escaping

When upgraded to LDAP `v3.4.14`, trust-policy validation enforces RFC 4514
escaping for `x509.subject` distinguished names. Previously tolerated unescaped
quotation marks, semicolons, angle brackets, and NULL characters in attribute
values are rejected. Escape these characters in the DN and escape the
backslashes again in JSON. An organization named `My "special" Org` uses:

```json
"x509.subject:C=US,ST=WA,O=My \\\"special\\\" Org"
```

Correctly escaped identities retain the same attribute values. Policies are
not rewritten automatically, and identity validation is not relaxed.

## Code of Conduct

This project has adopted the [CNCF Code of Conduct](https://github.com/cncf/foundation/blob/master/code-of-conduct.md). See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for further details.

## License

This project is covered under the Apache 2.0 license. You can read the license [here](LICENSE).
