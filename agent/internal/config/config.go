package config

import (
	"fmt"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

var safeID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)

const defaultComposeFile = "docker-compose.yml"

type Config struct {
	Endpoint       string
	AgentID        string
	AuthToken      string
	CAFile         string
	ServerName     string
	ProjectRoot    string
	ComposeFile    string
	ProtocolMajor  uint32
	HeartbeatEvery time.Duration
}

func Load() (Config, error) {
	composeFile := os.Getenv("DJANGOOPS_COMPOSE_FILE")
	if composeFile == "" {
		composeFile = defaultComposeFile
	}
	validatedComposeFile, err := validateComposeFile(composeFile)
	if err != nil {
		return Config{}, err
	}
	cfg := Config{
		Endpoint:       os.Getenv("DJANGOOPS_CONTROLPLANE_ENDPOINT"),
		AgentID:        os.Getenv("DJANGOOPS_AGENT_ID"),
		AuthToken:      os.Getenv("DJANGOOPS_AGENT_AUTH_TOKEN"),
		CAFile:         os.Getenv("DJANGOOPS_CONTROLPLANE_CA_FILE"),
		ServerName:     os.Getenv("DJANGOOPS_CONTROLPLANE_SERVER_NAME"),
		ProjectRoot:    os.Getenv("DJANGOOPS_PROJECT_ROOT"),
		ComposeFile:    validatedComposeFile,
		ProtocolMajor:  1,
		HeartbeatEvery: 5 * time.Second,
	}
	if raw := os.Getenv("DJANGOOPS_PROTOCOL_MAJOR"); raw != "" {
		value, parseErr := strconv.ParseUint(raw, 10, 32)
		if parseErr != nil {
			return Config{}, fmt.Errorf("invalid protocol major")
		}
		cfg.ProtocolMajor = uint32(value)
	}
	if raw := os.Getenv("DJANGOOPS_AGENT_HEARTBEAT"); raw != "" {
		value, parseErr := time.ParseDuration(raw)
		if parseErr != nil || value < 100*time.Millisecond || value > time.Minute {
			return Config{}, fmt.Errorf("invalid heartbeat interval")
		}
		cfg.HeartbeatEvery = value
	}
	if cfg.Endpoint == "" || cfg.AuthToken == "" || cfg.CAFile == "" || cfg.ServerName == "" || cfg.ProjectRoot == "" {
		return Config{}, fmt.Errorf("required agent configuration is missing")
	}
	if !safeID.MatchString(cfg.AgentID) {
		return Config{}, fmt.Errorf("invalid agent identity")
	}
	if _, err := os.Stat(cfg.CAFile); err != nil {
		return Config{}, fmt.Errorf("control-plane CA file is unavailable: %w", err)
	}
	root, err := filepath.Abs(cfg.ProjectRoot)
	if err != nil {
		return Config{}, fmt.Errorf("resolve project root: %w", err)
	}
	info, err := os.Stat(root)
	if err != nil || !info.IsDir() {
		return Config{}, fmt.Errorf("project root must be an existing directory")
	}
	cfg.ProjectRoot = filepath.Clean(root)
	return cfg, nil
}

func validateComposeFile(value string) (string, error) {
	if value == "" {
		return "", fmt.Errorf("Compose file path is required")
	}
	for _, char := range value {
		if char < 32 || char == 127 {
			return "", fmt.Errorf("Compose file path contains control characters")
		}
	}
	if path.IsAbs(value) {
		return "", fmt.Errorf("Compose file path must be project-relative")
	}
	for _, part := range strings.Split(value, "/") {
		if part == "." || part == ".." {
			return "", fmt.Errorf("Compose file path must not contain '.' or '..' segments")
		}
	}
	cleaned := path.Clean(value)
	if cleaned == "." {
		return "", fmt.Errorf("Compose file path is required")
	}
	return cleaned, nil
}
