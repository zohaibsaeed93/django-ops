package kubernetes

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const maxOutput = 8192

var dnsName = regexp.MustCompile(`^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$`)
var credentialRef = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9:/_.-]{2,127}$`)
var digestImage = regexp.MustCompile(`^.+@sha256:[0-9a-f]{64}$`)

var requiredPermissions = [][2]string{
	{"get", "deployments.apps"}, {"create", "deployments.apps"}, {"patch", "deployments.apps"}, {"delete", "deployments.apps"},
	{"get", "jobs.batch"}, {"create", "jobs.batch"}, {"delete", "jobs.batch"},
	{"get", "services"}, {"create", "services"}, {"patch", "services"}, {"delete", "services"},
	{"get", "secrets"}, {"create", "secrets"}, {"update", "secrets"}, {"patch", "secrets"}, {"delete", "secrets"},
	{"get", "ingresses.networking.k8s.io"}, {"create", "ingresses.networking.k8s.io"}, {"patch", "ingresses.networking.k8s.io"}, {"delete", "ingresses.networking.k8s.io"},
	{"get", "pods"}, {"list", "pods"}, {"get", "events"}, {"list", "events"},
}

type Request struct {
	Namespace        string
	ReleaseName      string
	ChartPath        string
	Image            string
	ValuesJSON       string
	CredentialRef    string
	MigrationTimeout time.Duration
	RolloutTimeout   time.Duration
	RollbackPolicy   string
	Mode             string
	PreviousRevision uint32
}

type Result struct {
	Status           string
	HelmRevision     uint32
	PreviousRevision uint32
	RollbackStatus   string
	ErrorCode        string
}

type Runner struct {
	ProjectRoot       string
	ResolveKubeconfig func(string) (string, error)
}

func ValidateRequest(req Request) error {
	if len(req.Namespace) == 0 || len(req.Namespace) > 63 || !dnsName.MatchString(req.Namespace) {
		return errors.New("invalid namespace")
	}
	if len(req.ReleaseName) == 0 || len(req.ReleaseName) > 53 || !dnsName.MatchString(req.ReleaseName) {
		return errors.New("invalid release name")
	}
	if !digestImage.MatchString(req.Image) {
		return errors.New("image must use sha256 digest")
	}
	if !credentialRef.MatchString(req.CredentialRef) {
		return errors.New("invalid credential reference")
	}
	if req.Mode != "validate" && req.Mode != "release" {
		return errors.New("invalid Kubernetes operation mode")
	}
	if req.RollbackPolicy != "allow" && req.RollbackPolicy != "block-after-migration" {
		return errors.New("invalid rollback policy")
	}
	if req.MigrationTimeout <= 0 || req.MigrationTimeout > 30*time.Minute || req.RolloutTimeout <= 0 || req.RolloutTimeout > 30*time.Minute {
		return errors.New("timeouts must be positive and bounded to 30 minutes")
	}
	var values map[string]any
	if err := json.Unmarshal([]byte(req.ValuesJSON), &values); err != nil {
		return errors.New("invalid values JSON")
	}
	return nil
}

func (r Runner) Run(ctx context.Context, req Request, progress func(string, string) error) Result {
	if err := ValidateRequest(req); err != nil {
		return Result{Status: "failed", ErrorCode: "INVALID_RELEASE_REQUEST"}
	}
	if r.ResolveKubeconfig == nil {
		return Result{Status: "failed", ErrorCode: "CREDENTIAL_RESOLVER_UNAVAILABLE"}
	}
	kubeconfig, err := r.ResolveKubeconfig(req.CredentialRef)
	if err != nil || kubeconfig == "" {
		return Result{Status: "failed", ErrorCode: "CLUSTER_CREDENTIAL_UNAVAILABLE"}
	}
	env := append(os.Environ(), "KUBECONFIG="+kubeconfig)
	for _, permission := range requiredPermissions {
		if !runStep(ctx, env, 20*time.Second, progress, "validate-permissions", "kubectl", "auth", "can-i", permission[0], permission[1], "-n", req.Namespace) {
			if ctx.Err() != nil {
				return Result{Status: "cancelled", ErrorCode: "CANCELLED"}
			}
			return Result{Status: "failed", ErrorCode: "KUBERNETES_PERMISSION_DENIED"}
		}
	}
	if !runStep(ctx, env, 20*time.Second, progress, "validate-helm", "helm", "version", "--short") {
		if ctx.Err() != nil {
			return Result{Status: "cancelled", ErrorCode: "CANCELLED"}
		}
		return Result{Status: "failed", ErrorCode: "HELM_UNAVAILABLE"}
	}
	if req.Mode == "validate" {
		return Result{Status: "succeeded"}
	}
	chart, err := r.safeChart(req.ChartPath)
	if err != nil {
		return Result{Status: "failed", ErrorCode: "INVALID_CHART_PATH"}
	}
	valuesFile, err := os.CreateTemp("", "djangoops-values-*.json")
	if err != nil {
		return Result{Status: "failed", ErrorCode: "VALUES_FILE_FAILED"}
	}
	valuesPath := valuesFile.Name()
	defer os.Remove(valuesPath)
	if err := valuesFile.Chmod(0o600); err != nil {
		valuesFile.Close()
		return Result{Status: "failed", ErrorCode: "VALUES_FILE_FAILED"}
	}
	if _, err := valuesFile.WriteString(req.ValuesJSON); err != nil {
		valuesFile.Close()
		return Result{Status: "failed", ErrorCode: "VALUES_FILE_FAILED"}
	}
	if err := valuesFile.Close(); err != nil {
		return Result{Status: "failed", ErrorCode: "VALUES_FILE_FAILED"}
	}
	imageArgs := []string{"--set-string", "image.repository=" + imageRepository(req.Image), "--set-string", "image.digest=" + imageDigest(req.Image)}
	if !runStep(ctx, env, 30*time.Second, progress, "render", "helm", append([]string{"lint", chart, "--values", valuesPath}, imageArgs...)...) {
		if ctx.Err() != nil {
			return Result{Status: "cancelled", ErrorCode: "CANCELLED"}
		}
		return Result{Status: "failed", ErrorCode: "RENDER_FAILED"}
	}
	if !runStep(ctx, env, 30*time.Second, progress, "render", "helm", append([]string{"template", req.ReleaseName, chart, "--namespace", req.Namespace, "--values", valuesPath}, imageArgs...)...) {
		if ctx.Err() != nil {
			return Result{Status: "cancelled", ErrorCode: "CANCELLED"}
		}
		return Result{Status: "failed", ErrorCode: "RENDER_FAILED"}
	}
	upgradeArgs := []string{"upgrade", "--install", req.ReleaseName, chart, "--namespace", req.Namespace, "--create-namespace=false", "--timeout", req.MigrationTimeout.String(), "--values", valuesPath}
	upgradeArgs = append(upgradeArgs, imageArgs...)
	if !runStep(ctx, env, req.MigrationTimeout, progress, "migrate-apply", "helm", upgradeArgs...) {
		if ctx.Err() != nil {
			return Result{Status: "cancelled", ErrorCode: "CANCELLED"}
		}
		return Result{Status: "failed", ErrorCode: "MIGRATION_OR_APPLY_FAILED"}
	}
	if runStep(ctx, env, req.RolloutTimeout, progress, "verify", "kubectl", "rollout", "status", "deployment/"+req.ReleaseName+"-web", "-n", req.Namespace, "--timeout="+req.RolloutTimeout.String()) {
		output, ok := runStructuredOutputStep(ctx, env, 20*time.Second, progress, "record-revision", "helm", "get", "metadata", req.ReleaseName, "--namespace", req.Namespace, "--output", "json")
		if !ok {
			return Result{Status: "failed", PreviousRevision: req.PreviousRevision, ErrorCode: "HELM_STATUS_FAILED"}
		}
		revision, err := helmRevision(output)
		if err != nil {
			return Result{Status: "failed", PreviousRevision: req.PreviousRevision, ErrorCode: "HELM_STATUS_INVALID"}
		}
		return Result{Status: "succeeded", HelmRevision: revision, PreviousRevision: req.PreviousRevision}
	}
	if ctx.Err() != nil {
		return Result{Status: "cancelled", ErrorCode: "CANCELLED"}
	}
	if req.RollbackPolicy != "allow" || req.PreviousRevision == 0 {
		return Result{Status: "rollback_blocked", PreviousRevision: req.PreviousRevision, RollbackStatus: "blocked", ErrorCode: "ROLLOUT_FAILED_DB_COMPATIBILITY_UNKNOWN"}
	}
	if !runStep(ctx, env, req.RolloutTimeout, progress, "rollback", "helm", "rollback", req.ReleaseName, strconv.FormatUint(uint64(req.PreviousRevision), 10), "--namespace", req.Namespace, "--wait", "--timeout", req.RolloutTimeout.String()) {
		return Result{Status: "failed", PreviousRevision: req.PreviousRevision, RollbackStatus: "failed", ErrorCode: "ROLLBACK_FAILED"}
	}
	if !runStep(ctx, env, req.RolloutTimeout, progress, "rollback-verify", "kubectl", "rollout", "status", "deployment/"+req.ReleaseName+"-web", "-n", req.Namespace, "--timeout="+req.RolloutTimeout.String()) {
		return Result{Status: "failed", PreviousRevision: req.PreviousRevision, RollbackStatus: "failed", ErrorCode: "ROLLBACK_VERIFY_FAILED"}
	}
	return Result{Status: "rolled_back", HelmRevision: req.PreviousRevision, PreviousRevision: req.PreviousRevision, RollbackStatus: "succeeded"}
}

func helmRevision(output []byte) (uint32, error) {
	var metadata map[string]json.RawMessage
	if err := json.Unmarshal(output, &metadata); err != nil {
		return 0, err
	}
	if raw, ok := metadata["revision"]; ok {
		var revision uint32
		if err := json.Unmarshal(raw, &revision); err != nil {
			return 0, err
		}
		if revision != 0 {
			return revision, nil
		}
	}
	// Older release-shaped JSON used a numeric version field for the release
	// revision. Helm metadata uses a string version field for the chart version,
	// so only accept this compatibility path when the JSON value is numeric.
	if raw, ok := metadata["version"]; ok {
		var revision uint32
		if err := json.Unmarshal(raw, &revision); err == nil && revision != 0 {
			return revision, nil
		}
	}
	return 0, errors.New("helm metadata response omitted release revision")
}

func runStep(ctx context.Context, env []string, timeout time.Duration, progress func(string, string) error, phase, name string, args ...string) bool {
	_, ok := runOutputStep(ctx, env, timeout, progress, phase, name, args...)
	return ok
}

func runOutputStep(ctx context.Context, env []string, timeout time.Duration, progress func(string, string) error, phase, name string, args ...string) ([]byte, bool) {
	if err := progress(phase, "running bounded "+phase+" step"); err != nil {
		return nil, false
	}
	stepCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := exec.CommandContext(stepCtx, name, args...)
	cmd.Env = env
	output, err := cmd.CombinedOutput()
	if err != nil {
		_ = redactOutput(output)
		return nil, false
	}
	if len(output) > maxOutput {
		output = output[:maxOutput]
	}
	return output, true
}

func runStructuredOutputStep(ctx context.Context, env []string, timeout time.Duration, progress func(string, string) error, phase, name string, args ...string) ([]byte, bool) {
	if err := progress(phase, "running bounded "+phase+" step"); err != nil {
		return nil, false
	}
	stepCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := exec.CommandContext(stepCtx, name, args...)
	cmd.Env = env
	output, err := cmd.Output()
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			_ = redactOutput(exitErr.Stderr)
		}
		return nil, false
	}
	if len(output) > maxOutput {
		return nil, false
	}
	return output, true
}

func (r Runner) safeChart(value string) (string, error) {
	root, err := filepath.Abs(r.ProjectRoot)
	if err != nil {
		return "", err
	}
	chart, err := filepath.Abs(filepath.Join(root, value))
	if err != nil {
		return "", err
	}
	if chart != root && !strings.HasPrefix(chart, root+string(filepath.Separator)) {
		return "", errors.New("chart path escapes project root")
	}
	return chart, nil
}
func imageRepository(image string) string { return strings.SplitN(image, "@sha256:", 2)[0] }
func imageDigest(image string) string {
	parts := strings.SplitN(image, "@", 2)
	if len(parts) != 2 {
		return ""
	}
	return parts[1]
}
func redactOutput(value []byte) string {
	if len(value) > maxOutput {
		value = value[:maxOutput]
	}
	text := string(value)
	for _, marker := range []string{"token=", "password=", "secret="} {
		if strings.Contains(strings.ToLower(text), marker) {
			return "[REDACTED]"
		}
	}
	return text
}
