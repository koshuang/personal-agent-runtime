package execution

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestCodexWorkerRunsFixedReadOnlyCommandWithDurableProvenance(t *testing.T) {
	root := t.TempDir()
	executable := writeCodexFixture(t, root, `#!/bin/sh
	printf '%s\n' "$PWD" > "$PWD/pwd"
	printf '%s\n' "$@" > "$PWD/args"
	printf '%s\n' "${INHERITED_SECRET-unset}" > "$PWD/secret"
	printf '%s\n' "$HOME" > "$PWD/home"
	printf '%s\n' "$CODEX_HOME" > "$PWD/codex-home"
printf 'trusted local summary\n'
`)
	t.Setenv("INHERITED_SECRET", "do-not-leak")

	worker, err := NewCodexWorker(CodexWorkerConfig{
		Executable:     executable,
		WorkspaceRoot:  root,
		Timeout:        time.Second,
		MaxOutputBytes: 1024,
	})
	if err != nil {
		t.Fatalf("new worker: %v", err)
	}

	result, err := worker.Run(context.Background(), WorkerInput{Prompt: "summarize this workspace"})
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if result.Status != "success" || result.Summary != "trusted local summary" {
		t.Fatalf("unexpected result: %+v", result)
	}
	if result.Checks["provider"] != "codex" || result.Checks["workspace_policy"] != "trusted-local-read-only" {
		t.Fatalf("missing provenance: %#v", result.Checks)
	}
	if result.Checks["exit_status"] != 0 || result.Checks["estimated_cost_usd"] != 0.0 {
		t.Fatalf("missing execution evidence: %#v", result.Checks)
	}
	if result.Checks["actual_cost_usd"] != nil || result.Checks["quota_units"] != nil || result.Checks["version"] != "unknown" {
		t.Fatalf("unknown telemetry must remain unknown: %#v", result.Checks)
	}
	canonicalRoot, err := filepath.EvalSymlinks(root)
	if err != nil {
		t.Fatalf("canonicalize fixture root: %v", err)
	}
	if got := mustReadFile(t, filepath.Join(root, "pwd")); strings.TrimSpace(got) != canonicalRoot {
		t.Fatalf("working directory=%q want %q", got, canonicalRoot)
	}
	if got := strings.TrimSpace(mustReadFile(t, filepath.Join(root, "secret"))); got != "unset" {
		t.Fatalf("ambient secret leaked to provider: %q", got)
	}
	if got := strings.TrimSpace(mustReadFile(t, filepath.Join(root, "home"))); got == canonicalRoot || got == "" {
		t.Fatalf("provider home=%q must be an isolated directory", got)
	}
	if got := strings.TrimSpace(mustReadFile(t, filepath.Join(root, "codex-home"))); got == "" || got != strings.TrimSpace(mustReadFile(t, filepath.Join(root, "home"))) {
		t.Fatalf("CODEX_HOME=%q must match isolated HOME", got)
	}
	args := strings.Split(strings.TrimSpace(mustReadFile(t, filepath.Join(root, "args"))), "\n")
	if len(args) != 8 || strings.Join(args[:5], "\n") != "exec\n--sandbox\nread-only\n--skip-git-repo-check\n--output-last-message" || args[5] == "" || args[6] != "--" || args[7] != "summarize this workspace" {
		t.Fatalf("unexpected codex arguments: %q", args)
	}
}

func TestCodexWorkerTreatsPromptAsDataNotArguments(t *testing.T) {
	root := t.TempDir()
	executable := writeCodexFixture(t, root, "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$PWD/args\"\nprintf ok\\n\n")
	worker, err := NewCodexWorker(CodexWorkerConfig{Executable: executable, WorkspaceRoot: root, Timeout: time.Second, MaxOutputBytes: 1024})
	if err != nil {
		t.Fatalf("new worker: %v", err)
	}
	prompt := "--dangerous-flag=from-task"
	if _, err := worker.Run(context.Background(), WorkerInput{Prompt: prompt}); err != nil {
		t.Fatalf("run: %v", err)
	}
	args := strings.Split(strings.TrimSpace(mustReadFile(t, filepath.Join(root, "args"))), "\n")
	if got, want := args[len(args)-1], prompt; got != want {
		t.Fatalf("prompt argument=%q want %q; args=%q", got, want, args)
	}
}

func TestCodexWorkerFailsOnTimeoutAndNonZeroExit(t *testing.T) {
	root := t.TempDir()
	t.Run("timeout", func(t *testing.T) {
		childPipe := filepath.Join(root, "child.pipe")
		if err := syscall.Mkfifo(childPipe, 0o600); err != nil {
			t.Fatalf("create child pipe: %v", err)
		}
		readyFile := filepath.Join(root, "child.ready")
		executable := writeCodexFixture(t, root, "#!/bin/sh\n(exec 3> \"$PWD/child.pipe\"; : > \"$PWD/child.ready\"; sleep 30 >&3) &\nwait\n")
		childExited := make(chan error, 1)
		go func() {
			reader, err := os.Open(childPipe)
			if err != nil {
				childExited <- err
				return
			}
			defer reader.Close()
			_, err = io.Copy(io.Discard, reader)
			childExited <- err
		}()
		worker, err := NewCodexWorker(CodexWorkerConfig{Executable: executable, WorkspaceRoot: root, Timeout: 500 * time.Millisecond, MaxOutputBytes: 1024})
		if err != nil {
			t.Fatalf("new worker: %v", err)
		}
		runDone := make(chan error, 1)
		go func() {
			_, err := worker.Run(context.Background(), WorkerInput{Prompt: "wait"})
			runDone <- err
		}()
		readyDeadline := time.Now().Add(2 * time.Second)
		for {
			if _, err := os.Stat(readyFile); err == nil {
				break
			} else if !os.IsNotExist(err) {
				t.Fatalf("stat child readiness: %v", err)
			}
			if time.Now().After(readyDeadline) {
				t.Fatal("descendant did not open the pipe before timeout")
			}
			time.Sleep(10 * time.Millisecond)
		}
		if err := <-runDone; err == nil {
			t.Fatal("expected timeout failure")
		}
		select {
		case err := <-childExited:
			if err != nil {
				t.Fatalf("read child pipe: %v", err)
			}
		case <-time.After(2 * time.Second):
			t.Fatal("descendant still holds the pipe after timeout")
		}
	})
	t.Run("non-zero", func(t *testing.T) {
		executable := writeCodexFixture(t, root, "#!/bin/sh\nprintf fail >&2\nexit 9\n")
		worker, err := NewCodexWorker(CodexWorkerConfig{Executable: executable, WorkspaceRoot: root, Timeout: time.Second, MaxOutputBytes: 1024})
		if err != nil {
			t.Fatalf("new worker: %v", err)
		}
		if _, err := worker.Run(context.Background(), WorkerInput{Prompt: "fail"}); err == nil {
			t.Fatal("expected non-zero failure")
		}
	})
}

func TestCodexWorkerFailureCannotCreateCompletedTask(t *testing.T) {
	root := t.TempDir()
	executable := writeCodexFixture(t, root, "#!/bin/sh\nprintf rejected >&2\nexit 7\n")
	worker, err := NewCodexWorker(CodexWorkerConfig{Executable: executable, WorkspaceRoot: root, Timeout: time.Second, MaxOutputBytes: 1024})
	if err != nil {
		t.Fatalf("new worker: %v", err)
	}
	service, _ := openTaskService(t)
	created, err := service.Create(t.Context(), "must not report false success")
	if err != nil {
		t.Fatalf("create task: %v", err)
	}
	err = NewRunner(service, worker, DeterministicVerifier{}).Run(t.Context(), created.ID)
	if err == nil {
		t.Fatal("expected runner failure")
	}
	got, err := service.Get(t.Context(), created.ID)
	if err != nil {
		t.Fatalf("get task: %v", err)
	}
	if got.Status != "failed" || got.Stage != "worker" || got.Result != nil {
		t.Fatalf("non-zero provider result created false success: %+v", got)
	}
}

func TestCodexWorkerRejectsUnsafeConfigurationAndOversizedOutput(t *testing.T) {
	root := t.TempDir()
	if _, err := NewCodexWorker(CodexWorkerConfig{Executable: "codex", WorkspaceRoot: root}); err == nil {
		t.Fatal("expected relative executable rejection")
	}
	if _, err := NewCodexWorker(CodexWorkerConfig{Executable: writeCodexFixture(t, root, "#!/bin/sh\n"), WorkspaceRoot: "  "}); err == nil {
		t.Fatal("expected blank workspace rejection")
	}
	executable := writeCodexFixture(t, root, "#!/bin/sh\nyes x | head -c 2048\n")
	worker, err := NewCodexWorker(CodexWorkerConfig{Executable: executable, WorkspaceRoot: root, Timeout: time.Second, MaxOutputBytes: 16})
	if err != nil {
		t.Fatalf("new worker: %v", err)
	}
	if _, err := worker.Run(context.Background(), WorkerInput{Prompt: "overflow"}); err == nil {
		t.Fatal("expected output limit failure")
	}
	messageExecutable := writeCodexFixture(t, root, "#!/bin/sh\nwhile [ $# -gt 0 ]; do if [ \"$1\" = \"--output-last-message\" ]; then shift; output=$1; fi; shift; done\nyes x | head -c 2048 > \"$output\"\n")
	messageWorker, err := NewCodexWorker(CodexWorkerConfig{Executable: messageExecutable, WorkspaceRoot: root, Timeout: time.Second, MaxOutputBytes: 16})
	if err != nil {
		t.Fatalf("new message worker: %v", err)
	}
	if _, err := messageWorker.Run(context.Background(), WorkerInput{Prompt: "overflow message"}); err == nil {
		t.Fatal("expected output-message limit failure")
	}
}

func writeCodexFixture(t *testing.T, root, body string) string {
	t.Helper()
	path := filepath.Join(root, "codex-fixture")
	if err := os.WriteFile(path, []byte(body), 0o755); err != nil {
		t.Fatalf("write fixture: %v", err)
	}
	return path
}

func mustReadFile(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return string(data)
}
