package kubernetes

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func validRequest() Request {
	return Request{Namespace: "project-a", ReleaseName: "django-app", ChartPath: "charts/djangoops", Image: "registry.example/app@sha256:" + strings.Repeat("a", 64), ValuesJSON: `{}`, CredentialRef: "file:cluster", MigrationTimeout: time.Minute, RolloutTimeout: time.Minute, RollbackPolicy: "block-after-migration", Mode: "release"}
}

func TestValidateRequest(t *testing.T) {
	req := validRequest()
	if err := ValidateRequest(req); err != nil {
		t.Fatalf("valid request rejected: %v", err)
	}
	req.Namespace = "../other"
	if err := ValidateRequest(req); err == nil {
		t.Fatal("unsafe namespace accepted")
	}
}
func TestRejectMutableImage(t *testing.T) {
	req := validRequest()
	req.Image = "registry.example/app:latest"
	if err := ValidateRequest(req); err == nil {
		t.Fatal("mutable image accepted")
	}
}
func TestRejectUnboundedTimeout(t *testing.T) {
	req := validRequest()
	req.RolloutTimeout = 31 * time.Minute
	if err := ValidateRequest(req); err == nil {
		t.Fatal("unbounded timeout accepted")
	}
}

func TestMigrationFailurePreventsRollout(t *testing.T) {
	runner, log := fakeRunner(t)
	t.Setenv("FAIL_HELM_UPGRADE", "1")
	result := runner.Run(context.Background(), validRequest(), noProgress)
	if result.Status != "failed" || result.ErrorCode != "MIGRATION_OR_APPLY_FAILED" {
		t.Fatalf("unexpected result: %+v", result)
	}
	if strings.Contains(readLog(t, log), "kubectl rollout") {
		t.Fatal("rollout ran after migration failure")
	}
}

func TestUnknownMigrationBlocksRollback(t *testing.T) {
	runner, log := fakeRunner(t)
	t.Setenv("FAIL_ROLLOUT", "always")
	req := validRequest()
	req.PreviousRevision = 1
	result := runner.Run(context.Background(), req, noProgress)
	if result.Status != "rollback_blocked" {
		t.Fatalf("unexpected result: %+v", result)
	}
	if strings.Contains(readLog(t, log), "helm rollback") {
		t.Fatal("unsafe rollback executed")
	}
}

func TestSafeMigrationRollsBackAndVerifies(t *testing.T) {
	runner, log := fakeRunner(t)
	t.Setenv("FAIL_ROLLOUT", "once")
	req := validRequest()
	req.PreviousRevision = 1
	req.RollbackPolicy = "allow"
	result := runner.Run(context.Background(), req, noProgress)
	if result.Status != "rolled_back" || result.HelmRevision != 1 {
		t.Fatalf("unexpected result: %+v", result)
	}
	text := readLog(t, log)
	if !strings.Contains(text, "helm rollback django-app 1") || strings.Count(text, "kubectl rollout") != 2 {
		t.Fatalf("rollback not verified:\n%s", text)
	}
}

func TestSuccessfulReleaseRecordsHelmRevision(t *testing.T) {
	runner, log := fakeRunner(t)
	result := runner.Run(context.Background(), validRequest(), noProgress)
	if result.Status != "succeeded" || result.HelmRevision != 2 {
		t.Fatalf("unexpected result: %+v", result)
	}
	if !strings.Contains(readLog(t, log), "helm get metadata django-app") {
		t.Fatal("release revision was not read from compact Helm metadata")
	}
}

func TestHelmRevisionAcceptsMetadataRevisionWithChartVersion(t *testing.T) {
	revision, err := helmRevision([]byte(`{"name":"django-app","version":"0.1.0","revision":3,"status":"deployed"}`))
	if err != nil || revision != 3 {
		t.Fatalf("unexpected metadata revision parse: revision=%d err=%v", revision, err)
	}
}

func TestHelmRevisionRetainsNumericReleaseVersionCompatibility(t *testing.T) {
	revision, err := helmRevision([]byte(`{"version":2,"info":{"status":"deployed"}}`))
	if err != nil || revision != 2 {
		t.Fatalf("unexpected release version parse: revision=%d err=%v", revision, err)
	}
}

func TestHelmRevisionDoesNotTreatChartVersionAsReleaseRevision(t *testing.T) {
	if _, err := helmRevision([]byte(`{"version":"0.1.0","status":"deployed"}`)); err == nil {
		t.Fatal("chart version was accepted as a release revision")
	}
}

func TestHelmRevisionRejectsMissingRevision(t *testing.T) {
	if _, err := helmRevision([]byte(`{"status":"deployed"}`)); err == nil {
		t.Fatal("Helm metadata without a release revision was accepted")
	}
}

func TestCancellationIsTerminal(t *testing.T) {
	runner, _ := fakeRunner(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	result := runner.Run(ctx, validRequest(), func(string, string) error { return ctx.Err() })
	if result.Status != "cancelled" {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func noProgress(string, string) error { return nil }

func fakeRunner(t *testing.T) (Runner, string) {
	t.Helper()
	root := t.TempDir()
	bin := filepath.Join(root, "bin")
	if err := os.Mkdir(bin, 0o755); err != nil {
		t.Fatal(err)
	}
	chart := filepath.Join(root, "charts", "djangoops")
	if err := os.MkdirAll(chart, 0o755); err != nil {
		t.Fatal(err)
	}
	kubeconfig := filepath.Join(root, "cluster")
	if err := os.WriteFile(kubeconfig, []byte("apiVersion: v1\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	log := filepath.Join(root, "commands.log")
	count := filepath.Join(root, "rollout-count")
	writeExecutable(t, filepath.Join(bin, "helm"), `#!/bin/sh
echo "helm $*" >> "$COMMAND_LOG"
case "$1" in
upgrade) [ -n "$FAIL_HELM_UPGRADE" ] && exit 1 ;;
get)
  if [ "$2" = "metadata" ]; then
    printf 'warning: synthetic Helm stderr noise\n' >&2
    printf '{"name":"django-app","version":"0.1.0","revision":2,"status":"deployed"}\n'
  fi
  ;;
esac
exit 0
`)
	writeExecutable(t, filepath.Join(bin, "kubectl"), `#!/bin/sh
echo "kubectl $*" >> "$COMMAND_LOG"
case "$*" in
*"rollout status"*)
  if [ "$FAIL_ROLLOUT" = "always" ]; then exit 1; fi
  if [ "$FAIL_ROLLOUT" = "once" ]; then
    n=0; [ -f "$ROLLOUT_COUNT" ] && n=$(cat "$ROLLOUT_COUNT"); n=$((n+1)); echo "$n" > "$ROLLOUT_COUNT"; [ "$n" -eq 1 ] && exit 1
  fi
  ;;
esac
exit 0
`)
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("COMMAND_LOG", log)
	t.Setenv("ROLLOUT_COUNT", count)
	return Runner{ProjectRoot: root, ResolveKubeconfig: func(string) (string, error) { return kubeconfig, nil }}, log
}

func writeExecutable(t *testing.T, path, content string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(content), 0o755); err != nil {
		t.Fatal(err)
	}
}
func readLog(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}
