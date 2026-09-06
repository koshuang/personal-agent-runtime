package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/koshuang/personal-agent-runtime/internal/artifact"
	"github.com/koshuang/personal-agent-runtime/internal/execution"
	"github.com/koshuang/personal-agent-runtime/internal/httpauth"
	"github.com/koshuang/personal-agent-runtime/internal/httplimit"
	"github.com/koshuang/personal-agent-runtime/internal/mcpserver"
	"github.com/koshuang/personal-agent-runtime/internal/task"
)

const (
	maxTaskRequestBytes = 32 * 1024
	maxPromptBytes      = task.MaxPromptBytes
	defaultRateLimitRPM = 120
	defaultRateBurst    = 30
)

type server struct{ tasks *task.Service }

type createTaskRequest struct {
	Prompt string `json:"prompt"`
}

func main() {
	dbPath := env("PAR_DB", ".par/runtime-go.db")
	if err := os.MkdirAll(filepath.Dir(dbPath), 0o755); err != nil {
		log.Fatal(err)
	}
	store, err := task.Open(dbPath)
	if err != nil {
		log.Fatal(err)
	}
	defer store.Close()

	artifactRoot := env("PAR_ARTIFACTS", ".par/artifacts")
	artifacts, err := artifact.NewStore(artifactRoot)
	if err != nil {
		log.Fatal(err)
	}

	workerName := env("PAR_WORKER", "echo")
	worker, err := buildWorker(workerName, env("PAR_WORKSPACE_ROOT", "."))
	if err != nil {
		log.Fatal(err)
	}
	maxCostUSD, err := nonNegativeFloatEnv("PAR_MAX_COST_USD", 0)
	if err != nil {
		log.Fatal(err)
	}
	router, err := execution.NewRouter(maxCostUSD, execution.RouteCandidate{
		Name:             workerName,
		Worker:           worker,
		EstimatedCostUSD: 0,
	})
	if err != nil {
		log.Fatal(err)
	}

	tasks := task.NewService(store)
	runner := execution.NewRunner(
		tasks,
		router,
		execution.DeterministicVerifier{},
		execution.WithArtifactWriter(artifacts),
	)
	dispatcher := execution.NewDispatcher(tasks, runner)
	dispatchCtx, cancelDispatch := context.WithCancel(context.Background())
	defer cancelDispatch()
	go dispatcher.Run(dispatchCtx)

	addr := env("PAR_ADDR", "127.0.0.1:8080")
	mcpToken := strings.TrimSpace(os.Getenv("PAR_MCP_BEARER_TOKEN"))
	if !httpauth.IsLoopbackAddress(addr) {
		log.Fatal("direct non-loopback PAR_ADDR is disabled; bind to loopback behind a trusted HTTPS gateway")
	}

	var authenticatedLimit *httplimit.Limiter
	rateLimitRPM := 0
	rateLimitBurst := 0
	if mcpToken != "" {
		rateLimitRPM, err = positiveIntEnv("PAR_RATE_LIMIT_RPM", defaultRateLimitRPM)
		if err != nil {
			log.Fatal(err)
		}
		rateLimitBurst, err = positiveIntEnv("PAR_RATE_LIMIT_BURST", defaultRateBurst)
		if err != nil {
			log.Fatal(err)
		}
		authenticatedLimit, err = httplimit.New(rateLimitRPM, rateLimitBurst)
		if err != nil {
			log.Fatal(err)
		}
	}

	protect := func(handler http.Handler) http.Handler {
		if authenticatedLimit != nil {
			handler = authenticatedLimit.Middleware(handler)
		}
		return httpauth.Bearer(mcpToken, handler)
	}

	s := &server{tasks: tasks}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", s.health)
	mux.Handle("POST /v1/tasks", protect(http.HandlerFunc(s.createTask)))
	mux.Handle("GET /v1/tasks/{id}", protect(http.HandlerFunc(s.getTask)))
	mux.Handle("GET /v1/tasks/{id}/result", protect(http.HandlerFunc(s.getResult)))
	mux.Handle("POST /v1/tasks/{id}/cancel", protect(http.HandlerFunc(s.cancelTask)))
	mux.Handle("/mcp", protect(mcpserver.NewSDK(tasks)))

	authMode := "disabled-loopback"
	if mcpToken != "" {
		authMode = "bearer"
	}
	log.Printf("personal-agent-runtime API listening on %s (db=%s, artifacts=%s, worker=%s, max_cost_usd=%g, mcp=/mcp, auth=%s, rate_limit_rpm=%d, rate_limit_burst=%d, dispatcher=enabled)", addr, dbPath, artifactRoot, workerName, maxCostUSD, authMode, rateLimitRPM, rateLimitBurst)
	httpServer := newHTTPServer(addr, mux)
	log.Fatal(httpServer.ListenAndServe())
}

func buildWorker(name, workspaceRoot string) (execution.Worker, error) {
	switch strings.ToLower(strings.TrimSpace(name)) {
	case "echo":
		return execution.EchoWorker{}, nil
	case "workspace":
		return execution.NewReadOnlyWorkspaceWorker(workspaceRoot)
	default:
		return nil, fmt.Errorf("unsupported PAR_WORKER %q", name)
	}
}

func nonNegativeFloatEnv(key string, fallback float64) (float64, error) {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback, nil
	}
	parsed, err := strconv.ParseFloat(value, 64)
	if err != nil || parsed < 0 || math.IsNaN(parsed) || math.IsInf(parsed, 0) {
		return 0, fmt.Errorf("%s must be a finite non-negative number", key)
	}
	return parsed, nil
}

func positiveIntEnv(key string, fallback int) (int, error) {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback, nil
	}
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed <= 0 {
		return 0, fmt.Errorf("%s must be a positive integer", key)
	}
	return parsed, nil
}

func newHTTPServer(addr string, handler http.Handler) *http.Server {
	return &http.Server{
		Addr:              addr,
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
}

func (s *server) health(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok", "mcp": "/mcp"})
}

func (s *server) createTask(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, maxTaskRequestBytes)
	var req createTaskRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		var maxBytesErr *http.MaxBytesError
		if errors.As(err, &maxBytesErr) {
			writeJSON(w, http.StatusRequestEntityTooLarge, map[string]any{"error": "request body too large"})
			return
		}
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid request body"})
		return
	}
	if strings.TrimSpace(req.Prompt) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "prompt is required"})
		return
	}
	if len(strings.TrimSpace(req.Prompt)) > task.MaxPromptBytes {
		writeJSON(w, http.StatusRequestEntityTooLarge, map[string]any{"error": task.ErrPromptTooLarge.Error()})
		return
	}
	t, err := s.tasks.Create(r.Context(), req.Prompt)
	if err != nil {
		if errors.Is(err, task.ErrPromptTooLarge) {
			writeJSON(w, http.StatusRequestEntityTooLarge, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"task_id": t.ID, "status": t.Status})
}

func (s *server) getTask(w http.ResponseWriter, r *http.Request) {
	t, err := s.tasks.Get(r.Context(), r.PathValue("id"))
	if err != nil {
		writeStoreError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, t)
}

func (s *server) getResult(w http.ResponseWriter, r *http.Request) {
	t, err := s.tasks.Result(r.Context(), r.PathValue("id"))
	if err != nil {
		if errors.Is(err, task.ErrResultNotReady) {
			writeJSON(w, http.StatusConflict, map[string]any{"task_id": t.ID, "status": t.Status, "error": "result not ready"})
			return
		}
		writeStoreError(w, err)
		return
	}
	var result any
	if err := json.Unmarshal([]byte(*t.Result), &result); err != nil {
		result = *t.Result
	}
	writeJSON(w, http.StatusOK, map[string]any{"task_id": t.ID, "status": t.Status, "result": result})
}

func (s *server) cancelTask(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if err := s.tasks.Cancel(r.Context(), id); err != nil {
		switch {
		case errors.Is(err, task.ErrNotFound):
			writeJSON(w, http.StatusNotFound, map[string]any{"error": "task not found"})
		case errors.Is(err, task.ErrTerminal):
			writeJSON(w, http.StatusConflict, map[string]any{"task_id": id, "error": "task is already terminal"})
		default:
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		}
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"task_id": id, "status": "canceled"})
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func writeStoreError(w http.ResponseWriter, err error) {
	if errors.Is(err, task.ErrNotFound) || errors.Is(err, os.ErrNotExist) || strings.Contains(err.Error(), "not found") {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "task not found"})
		return
	}
	writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
