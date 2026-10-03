module github.com/notaryproject/notation-go

go 1.26.0

require (
	github.com/go-ldap/ldap/v3 v3.4.11
	github.com/notaryproject/notation-core-go v1.3.0
	github.com/notaryproject/notation-plugin-framework-go v1.0.0
	github.com/notaryproject/tspclient-go v1.0.0
	github.com/opencontainers/go-digest v1.0.0
	github.com/opencontainers/image-spec v1.1.1
	github.com/veraison/go-cose v1.3.0
	golang.org/x/crypto v0.57.0
	golang.org/x/mod v0.41.0
	oras.land/oras-go/v2 v2.6.2
)

require (
	github.com/Azure/go-ntlmssp v0.1.1 // indirect
	github.com/fxamacker/cbor/v2 v2.9.4 // indirect
	github.com/go-asn1-ber/asn1-ber v1.5.8 // indirect
	github.com/golang-jwt/jwt/v4 v4.5.2 // indirect
	github.com/google/uuid v1.6.0 // indirect
	github.com/x448/float16 v0.8.4 // indirect
	golang.org/x/sync v0.22.0 // indirect
)

replace github.com/notaryproject/notation-core-go => github.com/yizha1/notation-core-go v1.3.1-trial.4
