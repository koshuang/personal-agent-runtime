package execution

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"
)

const (
	defaultCodexTimeout       = 2 * time.Minute
	defaultCodexMaxOutputSize = 64 * 1024
)

// CodexWorkerConfig is deliberately configuration-only: task input cannot
// select an executable, workspace, shell, or execution mode.
type CodexWorkerConfig struct {
	Executable     string
	WorkspaceRoot  string
	HomeDir        string
	Timeout        time.Duration
	MaxOutputBytes int
}

// CodexWorker runs a fixed, trusted-local, read-only Codex invocation. It is
// not an arbitrary shell adapter and intentionally does not inherit ambient
// credentials or environment variables.
type CodexWorker struct {
	executable     string
	workspaceRoot  string
	homeDir        string
	timeout        time.Duration
	maxOutputBytes int
}

func NewCodexWorker(config CodexWorkerConfig) (*CodexWorker, error) {
	executable := strings.TrimSpace(config.Executable)
	if executable == "" || !filepath.IsAbs(executable) {
		return nil, errors.New("codex executable must be an absolute path")
	}
	info, err := os.Stat(executable)
	if err != nil {
		return nil, fmt.Errorf("stat codex executable: %w", err)
	}
	if info.IsDir() || info.Mode()&0o111 == 0 {
		return nil, errors.New("codex executable must be an executable file")
	}

	workspaceRoot, err := filepath.Abs(strings.TrimSpace(config.WorkspaceRoot))
	if err != nil {
		return nil, fmt.Errorf("resolve workspace root: %w", err)
	}
	workspaceRoot, err = filepath.EvalSymlinks(workspaceRoot)
	if err != nil {
		return nil, fmt.Errorf("canonicalize workspace root: %w", err)
	}
	workspaceInfo, err := os.Stat(workspaceRoot)
	if err != nil {
		return nil, fmt.Errorf("stat workspace root: %w", err)
	}
	if !workspaceInfo.IsDir() {
		return nil, errors.New("workspace root must be a directory")
	}
	homeDir := strings.TrimSpace(config.HomeDir)
	if homeDir != "" {
		homeDir, err = filepath.Abs(homeDir)
		if err != nil {
			return nil, fmt.Errorf("resolve codex home: %w", err)
		}
		homeInfo, err := os.Stat(homeDir)
		if err != nil {
			return nil, fmt.Errorf("stat codex home: %w", err)
		}
		if !homeInfo.IsDir() {
			return nil, errors.New("codex home must be a directory")
		}
	}

	timeout := config.Timeout
	if timeout == 0 {
		timeout = defaultCodexTimeout
	}
	if timeout <= 0 {
		return nil, errors.New("codex timeout must be positive")
	}
	maxOutputBytes := config.MaxOutputBytes
	if maxOutputBytes == 0 {
		maxOutputBytes = defaultCodexMaxOutputSize
	}
	if maxOutputBytes <= 0 {
		return nil, errors.New("codex max output bytes must be positive")
	}

	return &CodexWorker{
		executable:     executable,
		workspaceRoot:  workspaceRoot,
		homeDir:        homeDir,
		timeout:        timeout,
		maxOutputBytes: maxOutputBytes,
	}, nil
}

func (w *CodexWorker) Run(ctx context.Context, input WorkerInput) (WorkerResult, error) {
	prompt := strings.TrimSpace(input.Prompt)
	if prompt == "" {
		return WorkerResult{}, errors.New("prompt is required")
	}
	if err := ctx.Err(); err != nil {
		return WorkerResult{}, err
	}

	resultFile, err := os.CreateTemp("", "par-codex-last-message-*")
	if err != nil {
		return WorkerResult{}, fmt.Errorf("create codex output file: %w", err)
	}
	resultPath := resultFile.Name()
	if err := resultFile.Close(); err != nil {
		_ = os.Remove(resultPath)
		return WorkerResult{}, fmt.Errorf("close codex output file: %w", err)
	}
	defer os.Remove(resultPath)
	homeDir := w.homeDir
	if homeDir == "" {
		homeDir, err = os.MkdirTemp("", "par-codex-home-*")
		if err != nil {
			return WorkerResult{}, fmt.Errorf("create isolated codex home: %w", err)
		}
		defer os.RemoveAll(homeDir)
	}

	runCtx, cancel := context.WithTimeout(ctx, w.timeout)
	defer cancel()
	stdout := &limitedBuffer{limit: w.maxOutputBytes}
	stderr := &limitedBuffer{limit: w.maxOutputBytes}
	command := exec.CommandContext(runCtx, w.executable,
		"exec", "--sandbox", "read-only", "--skip-git-repo-check",
		"--output-last-message", resultPath, "--", prompt,
	)
	command.Dir = w.workspaceRoot
	// Do not leak the server's ambient tokens, proxy settings, or home
	// directory into the provider process. Without an explicitly configured
	// dedicated Codex home, each run receives an empty, disposable home.
	command.Env = []string{"HOME=" + homeDir, "PATH=/usr/bin:/bin"}
	command.Stdout = stdout
	command.Stderr = stderr
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	command.Cancel = func() error {
		if command.Process == nil {
			return nil
		}
		return syscall.Kill(-command.Process.Pid, syscall.SIGKILL)
	}
	command.WaitDelay = time.Second

	if err := command.Run(); err != nil {
		if runCtx.Err() != nil {
			return WorkerResult{}, fmt.Errorf("codex execution: %w", runCtx.Err())
		}
		if errors.Is(err, errOutputLimit) {
			return WorkerResult{}, fmt.Errorf("codex execution output: %w", err)
		}
		// Provider stderr is deliberately not returned: the dispatcher logs worker
		// errors and provider diagnostics can contain sensitive task context.
		return WorkerResult{}, fmt.Errorf("codex execution failed: %w", err)
	}

	message, err := readCodexMessage(resultPath, w.maxOutputBytes)
	if err != nil {
		return WorkerResult{}, err
	}
	if message == "" {
		message = strings.TrimSpace(stdout.String())
	}
	if message == "" {
		return WorkerResult{}, errors.New("codex execution returned no summary")
	}
	return WorkerResult{
		Status:     "success",
		Summary:    message,
		Changes:    []string{},
		Artifacts:  []string{},
		Confidence: 1,
		Blockers:   []string{},
		Checks: map[string]any{
			"worker":             "codex",
			"provider":           "codex",
			"command":            "codex exec --sandbox read-only",
			"version":            "unknown",
			"workspace_policy":   "trusted-local-read-only",
			"exit_status":        0,
			"estimated_cost_usd": 0.0,
			"actual_cost_usd":    nil,
			"quota_units":        nil,
		},
	}, nil
}

func readCodexMessage(path string, limit int) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", fmt.Errorf("read codex output: %w", err)
	}
	if len(data) > limit {
		return "", fmt.Errorf("codex output: %w", errOutputLimit)
	}
	if !utf8.Valid(data) {
		return "", errors.New("codex output must be valid UTF-8")
	}
	return strings.TrimSpace(string(data)), nil
}

var errOutputLimit = errors.New("exceeds configured output limit")

type limitedBuffer struct {
	buffer bytes.Buffer
	limit  int
}

func (b *limitedBuffer) Write(data []byte) (int, error) {
	remaining := b.limit - b.buffer.Len()
	if remaining <= 0 {
		return 0, errOutputLimit
	}
	if len(data) > remaining {
		_, _ = b.buffer.Write(data[:remaining])
		return remaining, errOutputLimit
	}
	return b.buffer.Write(data)
}

func (b *limitedBuffer) String() string { return b.buffer.String() }
