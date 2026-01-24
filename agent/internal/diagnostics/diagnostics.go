package diagnostics

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"syscall"
)

const maxCapturedOutput = 8192
const defaultComposeFile = "docker-compose.yml"

var safeValue = regexp.MustCompile(`^[A-Za-z0-9_.+-]{1,80}$`)

type Check struct {
	Name   string
	Status string
	Detail string
}

type Runner struct {
	ProjectRoot string
	ComposeFile string
}

type cappedBuffer struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (c *cappedBuffer) Write(p []byte) (int, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	remaining := maxCapturedOutput - c.b.Len()
	if remaining > 0 {
		if len(p) > remaining {
			_, _ = c.b.Write(p[:remaining])
		} else {
			_, _ = c.b.Write(p)
		}
	}
	return len(p), nil
}

func (c *cappedBuffer) String() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.b.String()
}

func (r Runner) Run(ctx context.Context, releaseRoot string, progress func(string, string) error) ([]Check, string) {
	root, err := r.resolveRoot(releaseRoot)
	if err != nil {
		return []Check{{Name: "release_root", Status: "fail", Detail: "release root is outside configured project"}}, "unhealthy"
	}
	composeFile, err := r.resolveComposeFile(root)
	if err != nil {
		return []Check{{Name: "compose_file", Status: "fail", Detail: "Compose file path is invalid"}}, "unhealthy"
	}
	checks := make([]Check, 0, 9)
	if !emit(progress, "release", "validating active release") {
		return checks, "canceled"
	}
	checks = append(checks, Check{Name: "active_release", Status: "pass", Detail: filepath.Base(root)})

	if !emit(progress, "compose", "checking declared and running Compose services") {
		return checks, "canceled"
	}
	expectedOut, expectedErr := r.compose(ctx, composeFile, "config", "--services")
	runningOut, runningErr := r.compose(ctx, composeFile, "ps", "--services", "--filter", "status=running")
	if canceled(ctx) {
		return checks, "canceled"
	}
	expected := parseServices(expectedOut)
	running := parseServices(runningOut)
	if expectedErr != nil || runningErr != nil || len(expected) == 0 || !sameSet(expected, running) {
		checks = append(checks, Check{Name: "compose_services", Status: "fail", Detail: "declared services are not all running"})
	} else {
		checks = append(checks, Check{Name: "compose_services", Status: "pass", Detail: fmt.Sprintf("%d declared services running", len(expected))})
	}

	if !emit(progress, "django_check", "running Django deployment checks") {
		return checks, "canceled"
	}
	_, err = r.compose(ctx, composeFile, "exec", "-T", "web", "python", "manage.py", "check", "--deploy")
	if canceled(ctx) {
		return checks, "canceled"
	}
	checks = append(checks, commandCheck("django_check_deploy", err, "Django deployment checks passed", "Django deployment checks failed"))

	if !emit(progress, "database", "checking Django database connectivity") {
		return checks, "canceled"
	}
	dbOut, dbErr := r.compose(ctx, composeFile, "exec", "-T", "web", "python", "manage.py", "shell", "-c", "from django.db import connection; connection.ensure_connection(); print(connection.vendor)")
	if canceled(ctx) {
		return checks, "canceled"
	}
	vendor := controlledValue(dbOut)
	if dbErr != nil || vendor == "" {
		checks = append(checks, Check{Name: "database", Status: "fail", Detail: "Django database connectivity failed"})
	} else {
		checks = append(checks, Check{Name: "database", Status: "pass", Detail: "connected backend=" + vendor})
	}

	if !emit(progress, "migrations", "checking pending Django migrations") {
		return checks, "canceled"
	}
	_, err = r.compose(ctx, composeFile, "exec", "-T", "web", "python", "manage.py", "migrate", "--check", "--noinput")
	if canceled(ctx) {
		return checks, "canceled"
	}
	checks = append(checks, commandCheck("pending_migrations", err, "no pending migrations", "pending or unevaluable migrations"))

	if !emit(progress, "versions", "collecting Django and Python version metadata") {
		return checks, "canceled"
	}
	versionOut, versionErr := r.compose(ctx, composeFile, "exec", "-T", "web", "python", "manage.py", "shell", "-c", "import django,platform; print(django.get_version()+'|'+platform.python_version())")
	if canceled(ctx) {
		return checks, "canceled"
	}
	parts := strings.Split(strings.TrimSpace(versionOut), "|")
	if versionErr != nil || len(parts) != 2 || !safeValue.MatchString(parts[0]) || !safeValue.MatchString(parts[1]) {
		checks = append(checks, Check{Name: "versions", Status: "fail", Detail: "version metadata unavailable"})
	} else {
		checks = append(checks, Check{Name: "versions", Status: "pass", Detail: "django=" + parts[0] + " python=" + parts[1]})
	}

	for _, service := range []string{"celery", "celery_beat"} {
		if contains(expected, service) {
			status := "fail"
			detail := service + " configured but not running"
			if contains(running, service) {
				status = "pass"
				detail = service + " configured and running"
			}
			checks = append(checks, Check{Name: service, Status: status, Detail: detail})
		}
	}

	status := "healthy"
	for _, check := range checks {
		if check.Status != "pass" {
			status = "unhealthy"
			break
		}
	}
	return checks, status
}

func (r Runner) resolveRoot(requested string) (string, error) {
	if requested == "" {
		return "", errors.New("empty release root")
	}
	root := requested
	if !filepath.IsAbs(root) {
		root = filepath.Join(r.ProjectRoot, root)
	}
	root, err := filepath.Abs(root)
	if err != nil {
		return "", err
	}
	root = filepath.Clean(root)
	if _, statErr := os.Stat(root); statErr == nil {
		resolved, resolveErr := filepath.EvalSymlinks(root)
		if resolveErr != nil {
			return "", resolveErr
		}
		root = filepath.Clean(resolved)
	}
	rel, err := filepath.Rel(r.ProjectRoot, root)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", errors.New("release root escapes project root")
	}
	return root, nil
}

func (r Runner) resolveComposeFile(root string) (string, error) {
	value := r.ComposeFile
	if value == "" {
		value = defaultComposeFile
	}
	for _, char := range value {
		if char < 32 || char == 127 {
			return "", errors.New("Compose path contains control characters")
		}
	}
	if path.IsAbs(value) {
		return "", errors.New("Compose path must be relative")
	}
	for _, part := range strings.Split(value, "/") {
		if part == "." || part == ".." {
			return "", errors.New("Compose path contains traversal segments")
		}
	}
	cleaned := path.Clean(value)
	if cleaned == "." {
		return "", errors.New("Compose path is empty")
	}
	candidate := filepath.Join(root, filepath.FromSlash(cleaned))
	rel, err := filepath.Rel(root, candidate)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", errors.New("Compose path escapes release root")
	}
	return candidate, nil
}

func (r Runner) compose(ctx context.Context, composeFile string, args ...string) (string, error) {
	full := append([]string{"compose", "-f", composeFile}, args...)
	return runCommand(ctx, "docker", full...)
}

func runCommand(ctx context.Context, name string, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	var output cappedBuffer
	cmd.Stdout = &output
	cmd.Stderr = &output
	if err := cmd.Start(); err != nil {
		return "", err
	}
	done := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
			_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
		case <-done:
		}
	}()
	err := cmd.Wait()
	close(done)
	return output.String(), err
}

func parseServices(raw string) []string {
	seen := map[string]bool{}
	for _, line := range strings.Split(raw, "\n") {
		value := strings.TrimSpace(line)
		if value != "" && safeValue.MatchString(value) {
			seen[value] = true
		}
	}
	values := make([]string, 0, len(seen))
	for value := range seen {
		values = append(values, value)
	}
	sort.Strings(values)
	return values
}

func sameSet(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func contains(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

func controlledValue(raw string) string {
	value := strings.TrimSpace(raw)
	if safeValue.MatchString(value) {
		return value
	}
	return ""
}

func commandCheck(name string, err error, passDetail, failDetail string) Check {
	if err == nil {
		return Check{Name: name, Status: "pass", Detail: passDetail}
	}
	return Check{Name: name, Status: "fail", Detail: failDetail}
}

func emit(progress func(string, string) error, phase, message string) bool {
	return progress(phase, message) == nil
}

func canceled(ctx context.Context) bool {
	return errors.Is(ctx.Err(), context.Canceled) || errors.Is(ctx.Err(), context.DeadlineExceeded)
}
