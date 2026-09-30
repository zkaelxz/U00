// The Caddy that the Windows installer bundles for household access
// (docs/windows-installer-design.md, "Boot services"). It is stock Caddy
// plus the rate_limit module, which deploy/caddy/Caddyfile.template needs
// and the official release binaries don't include. go.mod and go.sum pin
// every module by hash; installer/build_installer.py builds it for
// windows/amd64 and refuses a binary whose SHA-256 isn't CADDY_SHA256.
package main

import (
	caddycmd "github.com/caddyserver/caddy/v2/cmd"

	_ "github.com/caddyserver/caddy/v2/modules/standard"
	_ "github.com/mholt/caddy-ratelimit"
)

func main() {
	caddycmd.Main()
}
