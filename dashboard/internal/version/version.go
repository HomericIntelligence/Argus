// Package version exposes the build-injected Atlas version string.
package version

// Version is the Atlas build version. It defaults to "dev" and is overridden
// at link time via -ldflags "-X .../version.Version=<value>".
var Version = "dev"
