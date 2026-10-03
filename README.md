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
- [Fork-only draft trial](#fork-only-draft-trial)
- [Code of Conduct](#code-of-conduct)
- [License](#license)
 
## Documentation

Library documentation is available at [Go Reference](https://pkg.go.dev/github.com/notaryproject/notation-go).

## Fork-only draft trial

The `yizha1/notation-go` trial pins approved core commit
`03171674c94728622c5b1534b5e3396fcb468733` through a versioned fork replacement,
while preserving canonical Notary module paths and the released core requirement
in `go.mod`. Go resolves this commit to its existing `v1.3.1-trial.4` tag.
The core GitHub release may remain an unpublished draft for this explicit trial
exception. Official releases must use public released dependency versions, not
trial commits or fork replacements.
Dependency replacements are not inherited by consumers, so downstream CLI trial
modules must explicitly replace both Notary libraries with their tested forks.

After separately approving the candidate and enabling `PATCH_TRIAL_ENABLED`,
pushing a signed `v*-trial.*` tag runs the fork trial workflow. It checks that the
core replacement resolves to the approved commit, then reuses the immutable core
workflow revision to verify modules, run vet/race tests and govulncheck on patched
Go 1.26 and stable, and create an unpublished source draft with checksums.

This workflow does not detect new dependency versions, update manifests, merge
dependency PRs, select a patch version, or implement monthly scheduling.
For the official process, Dependabot detects released versions and opens
`go.mod`/`go.sum` update PRs. After review, CI, and merge, each repository's own
release cadence picks up those changes. Dependabot is currently configured to
check weekly against the default branch; stable-line automation must target the
intended release branch. There is no custom producer-workflow monitoring,
draft-release polling, or cross-repository dispatch.

## Code of Conduct

This project has adopted the [CNCF Code of Conduct](https://github.com/cncf/foundation/blob/master/code-of-conduct.md). See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for further details.

## License

This project is covered under the Apache 2.0 license. You can read the license [here](LICENSE).
