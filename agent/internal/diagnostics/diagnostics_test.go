package diagnostics

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestResolveRootRejectsEscape(t *testing.T) {
	root := t.TempDir()
	runner := Runner{ProjectRoot: root}
	if _, err := runner.resolveRoot("../outside"); err == nil {
		t.Fatal("expected path escape rejection")
	}
}

func TestRunReportsStructuredHealthyDiagnostics(t *testing.T) {
	root := t.TempDir()
	bin := filepath.Join(t.TempDir(), "docker")
	script := `#!/bin/sh
case "$*" in
  *"config --services"*) printf 'web\npostgres\nredis\ncelery\ncelery_beat\n' ;;
  *"ps --services"*) printf 'web\npostgres\nredis\ncelery\ncelery_beat\n' ;;
  *"connection.ensure_connection"*) printf 'postgresql\n' ;;
  *"django.get_version"*) printf '5.1.7|3.12.9\n' ;;
  *) exit 0 ;;
esac
`
	if err := os.WriteFile(bin, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "docker-compose.yml"), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", filepath.Dir(bin)+string(os.PathListSeparator)+os.Getenv("PATH"))
	checks, status := (Runner{ProjectRoot: root}).Run(context.Background(), ".", func(string, string) error { return nil })
	if status != "healthy" {
		t.Fatalf("expected healthy status, got %q: %#v", status, checks)
	}
	if len(checks) < 7 {
		t.Fatalf("expected rich diagnostics, got %#v", checks)
	}
}
