package execution

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"unicode/utf8"
)

const (
	maxWorkspaceReadBytes = 256 * 1024
	maxWorkspaceListItems = 200
)

type ReadOnlyWorkspaceWorker struct {
	root *os.Root
}

func NewReadOnlyWorkspaceWorker(root string) (*ReadOnlyWorkspaceWorker, error) {
	root = strings.TrimSpace(root)
	if root == "" {
		return nil, errors.New("workspace root is required")
	}
	abs, err := filepath.Abs(root)
	if err != nil {
		return nil, fmt.Errorf("resolve workspace root: %w", err)
	}
	rootHandle, err := os.OpenRoot(abs)
	if err != nil {
		return nil, fmt.Errorf("open workspace root: %w", err)
	}
	info, err := rootHandle.Stat(".")
	if err != nil {
		_ = rootHandle.Close()
		return nil, fmt.Errorf("stat workspace root: %w", err)
	}
	if !info.IsDir() {
		_ = rootHandle.Close()
		return nil, errors.New("workspace root must be a directory")
	}
	return &ReadOnlyWorkspaceWorker{root: rootHandle}, nil
}

func (w *ReadOnlyWorkspaceWorker) Run(ctx context.Context, input WorkerInput) (WorkerResult, error) {
	if err := ctx.Err(); err != nil {
		return WorkerResult{}, err
	}
	command, arg, ok := strings.Cut(strings.TrimSpace(input.Prompt), " ")
	if !ok || strings.TrimSpace(arg) == "" {
		return WorkerResult{}, errors.New("workspace command must be `read <relative-path>` or `list <relative-path>`")
	}
	arg = strings.TrimSpace(arg)

	switch strings.ToLower(command) {
	case "read":
		return w.read(ctx, arg)
	case "list":
		return w.list(ctx, arg)
	default:
		return WorkerResult{}, errors.New("unsupported workspace command")
	}
}

func (w *ReadOnlyWorkspaceWorker) read(ctx context.Context, relative string) (WorkerResult, error) {
	clean, err := cleanWorkspacePath(relative)
	if err != nil {
		return WorkerResult{}, err
	}
	if err := ctx.Err(); err != nil {
		return WorkerResult{}, err
	}

	// os.Root performs traversal-resistant, root-anchored path resolution. The
	// returned descriptor remains bound to the opened file even if an ancestor
	// or leaf is concurrently replaced after this call.
	f, err := w.root.Open(clean)
	if err != nil {
		return WorkerResult{}, fmt.Errorf("open workspace file: %w", err)
	}
	defer f.Close()

	info, err := f.Stat()
	if err != nil {
		return WorkerResult{}, fmt.Errorf("stat workspace file: %w", err)
	}
	if !info.Mode().IsRegular() {
		return WorkerResult{}, errors.New("read target must be a regular file")
	}

	data, err := io.ReadAll(io.LimitReader(f, maxWorkspaceReadBytes+1))
	if err != nil {
		return WorkerResult{}, fmt.Errorf("read workspace file: %w", err)
	}
	if len(data) > maxWorkspaceReadBytes {
		return WorkerResult{}, fmt.Errorf("file exceeds read limit of %d bytes", maxWorkspaceReadBytes)
	}
	if bytes.IndexByte(data, 0) >= 0 || !utf8.Valid(data) {
		return WorkerResult{}, errors.New("binary or non-UTF-8 files are not supported")
	}
	if err := ctx.Err(); err != nil {
		return WorkerResult{}, err
	}

	hash := sha256.Sum256(data)
	return WorkerResult{
		Status:  "success",
		Summary: fmt.Sprintf("Read %s (%d bytes)", filepath.ToSlash(clean), len(data)),
		Changes: []string{},
		Checks: map[string]any{
			"worker":       "readonly-workspace",
			"operation":    "read",
			"path":         filepath.ToSlash(clean),
			"bytes":        len(data),
			"sha256":       hex.EncodeToString(hash[:]),
			"content":      string(data),
			"max_cost_usd": 0,
		},
		Artifacts:  []string{},
		Confidence: 1,
		Blockers:   []string{},
	}, nil
}

func (w *ReadOnlyWorkspaceWorker) list(ctx context.Context, relative string) (WorkerResult, error) {
	clean, err := cleanWorkspacePath(relative)
	if err != nil {
		return WorkerResult{}, err
	}
	if err := ctx.Err(); err != nil {
		return WorkerResult{}, err
	}

	dir, err := w.root.Open(clean)
	if err != nil {
		return WorkerResult{}, fmt.Errorf("open workspace directory: %w", err)
	}
	defer dir.Close()
	info, err := dir.Stat()
	if err != nil {
		return WorkerResult{}, fmt.Errorf("stat workspace directory: %w", err)
	}
	if !info.IsDir() {
		return WorkerResult{}, errors.New("list target must be a directory")
	}

	entries, err := dir.ReadDir(maxWorkspaceListItems + 1)
	if err != nil && !errors.Is(err, io.EOF) {
		return WorkerResult{}, fmt.Errorf("list workspace directory: %w", err)
	}
	if len(entries) > maxWorkspaceListItems {
		entries = entries[:maxWorkspaceListItems]
	}
	items := make([]string, 0, len(entries))
	for _, entry := range entries {
		if err := ctx.Err(); err != nil {
			return WorkerResult{}, err
		}
		name := entry.Name()
		if entry.IsDir() {
			name += "/"
		}
		items = append(items, name)
	}
	sort.Strings(items)
	return WorkerResult{
		Status:  "success",
		Summary: fmt.Sprintf("Listed %s (%d entries)", filepath.ToSlash(clean), len(items)),
		Changes: []string{},
		Checks: map[string]any{
			"worker":       "readonly-workspace",
			"operation":    "list",
			"path":         filepath.ToSlash(clean),
			"entries":      items,
			"max_cost_usd": 0,
		},
		Artifacts:  []string{},
		Confidence: 1,
		Blockers:   []string{},
	}, nil
}

func cleanWorkspacePath(relative string) (string, error) {
	relative = strings.TrimSpace(relative)
	if relative == "" || filepath.IsAbs(relative) {
		return "", errors.New("workspace path must be relative")
	}
	clean := filepath.Clean(filepath.FromSlash(relative))
	if clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
		return "", errors.New("workspace path escapes root")
	}
	return clean, nil
}
