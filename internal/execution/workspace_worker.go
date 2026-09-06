package execution

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

const (
	maxWorkspaceReadBytes = 256 * 1024
	maxWorkspaceListItems = 200
)

type ReadOnlyWorkspaceWorker struct {
	root string
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
	resolved, err := filepath.EvalSymlinks(abs)
	if err != nil {
		return nil, fmt.Errorf("resolve workspace symlinks: %w", err)
	}
	info, err := os.Stat(resolved)
	if err != nil {
		return nil, fmt.Errorf("stat workspace root: %w", err)
	}
	if !info.IsDir() {
		return nil, errors.New("workspace root must be a directory")
	}
	return &ReadOnlyWorkspaceWorker{root: resolved}, nil
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
	path, err := w.resolve(relative)
	if err != nil {
		return WorkerResult{}, err
	}
	info, err := os.Stat(path)
	if err != nil {
		return WorkerResult{}, fmt.Errorf("stat workspace file: %w", err)
	}
	if !info.Mode().IsRegular() {
		return WorkerResult{}, errors.New("read target must be a regular file")
	}
	if info.Size() > maxWorkspaceReadBytes {
		return WorkerResult{}, fmt.Errorf("file exceeds read limit of %d bytes", maxWorkspaceReadBytes)
	}
	if err := ctx.Err(); err != nil {
		return WorkerResult{}, err
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return WorkerResult{}, fmt.Errorf("read workspace file: %w", err)
	}
	if strings.IndexByte(string(data), 0) >= 0 {
		return WorkerResult{}, errors.New("binary files are not supported")
	}
	hash := sha256.Sum256(data)
	return WorkerResult{
		Status:  "success",
		Summary: fmt.Sprintf("Read %s (%d bytes)", filepath.ToSlash(relative), len(data)),
		Changes: []string{},
		Checks: map[string]any{
			"worker":       "readonly-workspace",
			"operation":    "read",
			"path":         filepath.ToSlash(relative),
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
	path, err := w.resolve(relative)
	if err != nil {
		return WorkerResult{}, err
	}
	entries, err := os.ReadDir(path)
	if err != nil {
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
		Summary: fmt.Sprintf("Listed %s (%d entries)", filepath.ToSlash(relative), len(items)),
		Changes: []string{},
		Checks: map[string]any{
			"worker":       "readonly-workspace",
			"operation":    "list",
			"path":         filepath.ToSlash(relative),
			"entries":      items,
			"max_cost_usd": 0,
		},
		Artifacts:  []string{},
		Confidence: 1,
		Blockers:   []string{},
	}, nil
}

func (w *ReadOnlyWorkspaceWorker) resolve(relative string) (string, error) {
	relative = strings.TrimSpace(relative)
	if relative == "" || filepath.IsAbs(relative) {
		return "", errors.New("workspace path must be relative")
	}
	clean := filepath.Clean(filepath.FromSlash(relative))
	if clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
		return "", errors.New("workspace path escapes root")
	}
	candidate := filepath.Join(w.root, clean)
	resolved, err := filepath.EvalSymlinks(candidate)
	if err != nil {
		return "", fmt.Errorf("resolve workspace path: %w", err)
	}
	rel, err := filepath.Rel(w.root, resolved)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", errors.New("workspace path escapes root")
	}
	return resolved, nil
}
