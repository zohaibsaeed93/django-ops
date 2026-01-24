package config

import (
	"os"
	"path/filepath"
	"testing"
)

func setValidEnv(t *testing.T) {
	t.Helper()
	t.Setenv("DJANGOOPS_CONTROLPLANE_ENDPOINT", "localhost:8443")
	t.Setenv("DJANGOOPS_AGENT_ID", "agent-1")
	t.Setenv("DJANGOOPS_AGENT_AUTH_TOKEN", "synthetic")
	ca := filepath.Join(t.TempDir(), "ca.pem")
	if err := os.WriteFile(ca, []byte("test"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("DJANGOOPS_CONTROLPLANE_CA_FILE", ca)
	t.Setenv("DJANGOOPS_CONTROLPLANE_SERVER_NAME", "localhost")
	t.Setenv("DJANGOOPS_PROJECT_ROOT", t.TempDir())
}

func TestLoadRejectsUnsafeIdentity(t *testing.T) {
	setValidEnv(t)
	t.Setenv("DJANGOOPS_AGENT_ID", "../../escape")
	if _, err := Load(); err == nil {
		t.Fatal("expected unsafe agent identity to be rejected")
	}
}

func TestLoadPreservesValidatedCustomComposeFile(t *testing.T) {
	setValidEnv(t)
	t.Setenv("DJANGOOPS_COMPOSE_FILE", "ops/compose.prod.yml")
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.ComposeFile != "ops/compose.prod.yml" {
		t.Fatalf("unexpected Compose file: %q", cfg.ComposeFile)
	}
}

func TestLoadDefaultsComposeFile(t *testing.T) {
	setValidEnv(t)
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.ComposeFile != defaultComposeFile {
		t.Fatalf("unexpected default Compose file: %q", cfg.ComposeFile)
	}
}

func TestLoadRejectsUnsafeComposeFiles(t *testing.T) {
	for _, value := range []string{"/tmp/compose.yml", "../compose.yml", "ops/../compose.yml", "ops/./compose.yml", "bad\nname.yml"} {
		t.Run(value, func(t *testing.T) {
			setValidEnv(t)
			t.Setenv("DJANGOOPS_COMPOSE_FILE", value)
			if _, err := Load(); err == nil {
				t.Fatal("expected unsafe Compose path to be rejected")
			}
		})
	}
}
