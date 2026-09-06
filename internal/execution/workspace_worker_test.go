package execution

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReadOnlyWorkspaceWorkerReadsFile(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "README.md"), []byte("hello runtime\n"), 0o644); err != nil {
		t.Fatalf("write fixture: %v", err)
	}
	worker, err := NewReadOnlyWorkspaceWorker(root)
	if err != nil {
		t.Fatalf("new worker: %v", err)
	}
	result, err := worker.Run(context.Background(), WorkerInput{Prompt: "read README.md"})
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if result.Status != "success" || result.Checks["worker"] != "readonly-workspace" {
		t.Fatalf("unexpected result: %+v", result)
	}
	if result.Checks["content"] != "hello runtime\n" {
		t.Fatalf("unexpected content: %#v", result.Checks["content"])
	}
	if result.Checks["max_cost_usd"] != 0 {
		t.Fatalf("expected zero cost marker: %+v", result.Checks)
	}
}

func TestReadOnlyWorkspaceWorkerListsDirectory(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "b.txt"), []byte("b"), 0o644); err != nil {
		t.Fatalf("write fixture: %v", err)
	}
	if err := os.WriteFile(filepath.Join(root, "a.txt"), []byte("a"), 0o644); err != nil {
		t.Fatalf("write fixture: %v", err)
	}
	if err := os.Mkdir(filepath.Join(root, "docs"), 0o755); err != nil {
		t.Fatalf("mkdir fixture: %v", err)
	}
	worker, err := NewReadOnlyWorkspaceWorker(root)
	if err != nil {
		t.Fatalf("new worker: %v", err)
	}
	result, err := worker.Run(context.Background(), WorkerInput{Prompt: "list ."})
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	entries, ok := result.Checks["entries"].([]string)
	if !ok {
		t.Fatalf("unexpected entries type: %#v", result.Checks["entries"])
	}
	got := strings.Join(entries, ",")
	if got != "a.txt,b.txt,docs/" {
		t.Fatalf("entries=%q", got)
	}
}

func TestReadOnlyWorkspaceWorkerRejectsTraversal(t *testing.T) {
	root := t.TempDir()
	worker, err := NewReadOnlyWorkspaceWorker(root)
	if err != nil {
		t.Fatalf("new worker: %v", err)
	}
	if _, err := worker.Run(context.Background(), WorkerInput{Prompt: "read ../secret"}); err == nil {
		t.Fatal("expected traversal rejection")
	}
}

func TestReadOnlyWorkspaceWorkerRejectsSymlinkEscape(t *testing.T) {
	root := t.TempDir()
	outside := t.TempDir()
	secret := filepath.Join(outside, "secret.txt")
	if err := os.WriteFile(secret, []byte("secret"), 0o644); err != nil {
		t.Fatalf("write secret: %v", err)
	}
	if err := os.Symlink(secret, filepath.Join(root, "link.txt")); err != nil {
		t.Skipf("symlink unavailable: %v", err)
	}
	worker, err := NewReadOnlyWorkspaceWorker(root)
	if err != nil {
		t.Fatalf("new worker: %v", err)
	}
	if _, err := worker.Run(context.Background(), WorkerInput{Prompt: "read link.txt"}); err == nil {
		t.Fatal("expected symlink escape rejection")
	}
}

func TestReadOnlyWorkspaceWorkerRejectsOversizedFile(t *testing.T) {
	root := t.TempDir()
	payload := make([]byte, maxWorkspaceReadBytes+1)
	if err := os.WriteFile(filepath.Join(root, "large.txt"), payload, 0o644); err != nil {
		t.Fatalf("write fixture: %v", err)
	}
	worker, err := NewReadOnlyWorkspaceWorker(root)
	if err != nil {
		t.Fatalf("new worker: %v", err)
	}
	if _, err := worker.Run(context.Background(), WorkerInput{Prompt: "read large.txt"}); err == nil {
		t.Fatal("expected read limit rejection")
	}
}
